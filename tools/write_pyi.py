from argparse import ArgumentParser
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
import textwrap

from mako.pygen import PythonPrinter

from alembic.operations.base import Operations
from alembic.runtime.environment import EnvironmentContext
from alembic.script.write_hooks import console_scripts
from alembic.util.compat import inspect_formatargspec
from alembic.util.compat import inspect_getfullargspec

IGNORE_ITEMS = {
    "op": {"context", "create_module_class_proxy"},
    "context": {
        "config",
        "create_module_class_proxy",
        "get_impl",
        "requires_connection",
        "script",
    },
}


def generate_pyi_for_proxy(
    cls: type,
    progname: str,
    source_path: Path,
    destination_path: Path,
    ignore_output: bool,
    ignore_items: set,
):

    imports = []
    read_imports = False
    with open(source_path) as read_file:
        for line in read_file:
            if line.startswith("# ### this file stubs are generated by"):
                read_imports = True
            elif line.startswith("### end imports ###"):
                read_imports = False
                break
            elif read_imports:
                imports.append(line.rstrip())

    with open(destination_path, "w") as buf:
        printer = PythonPrinter(buf)

        printer.writeline(
            f"# ### this file stubs are generated by {progname} "
            "- do not edit ###"
        )
        for line in imports:
            buf.write(line + "\n")
        printer.writeline("### end imports ###")
        buf.write("\n\n")

        for name in dir(cls):
            if name.startswith("_") or name in ignore_items:
                continue
            meth = getattr(cls, name)
            if callable(meth):
                _generate_stub_for_meth(cls, name, printer)
            else:
                _generate_stub_for_attr(cls, name, printer)

        printer.close()

    console_scripts(
        str(destination_path),
        {"entrypoint": "zimports", "options": "-e"},
        ignore_output=ignore_output,
    )
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    console_scripts(
        str(destination_path),
        {"entrypoint": "black", "options": f"--config {pyproject}"},
        ignore_output=ignore_output,
    )


def _generate_stub_for_attr(cls, name, printer):
    printer.writeline(f"{name}: Any")


def _generate_stub_for_meth(cls, name, printer):

    fn = getattr(cls, name)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__

    spec = inspect_getfullargspec(fn)

    name_args = spec[0]
    assert name_args[0:1] == ["self"] or name_args[0:1] == ["cls"]

    name_args[0:1] = []

    def _formatannotation(annotation, base_module=None):
        if getattr(annotation, "__module__", None) == "typing":
            retval = repr(annotation).replace("typing.", "")
        elif isinstance(annotation, type):
            if annotation.__module__ in ("builtins", base_module):
                retval = annotation.__qualname__
            else:
                retval = annotation.__module__ + "." + annotation.__qualname__
        else:
            retval = repr(annotation)

        retval = re.sub(
            r'ForwardRef\(([\'"].+?[\'"])\)', lambda m: m.group(1), retval
        )
        retval = re.sub("NoneType", "None", retval)
        return retval

    argspec = inspect_formatargspec(
        *spec, formatvalue=lambda value: "", formatannotation=_formatannotation
    )

    func_text = textwrap.dedent(
        """\
    def %(name)s%(argspec)s:
        '''%(doc)s'''
    """
        % {
            "name": name,
            "argspec": argspec,
            "doc": fn.__doc__,
        }
    )

    printer.write_indented_block(func_text)


def run_file(
    source_path: Path, cls_to_generate: type, stdout: bool, ignore_items: set
):
    progname = Path(sys.argv[0]).as_posix()
    if not stdout:
        generate_pyi_for_proxy(
            cls_to_generate,
            progname,
            source_path=source_path,
            destination_path=source_path,
            ignore_output=False,
            ignore_items=ignore_items,
        )
    else:
        with NamedTemporaryFile(delete=False, suffix=".pyi") as f:
            f.close()
            generate_pyi_for_proxy(
                cls_to_generate,
                progname,
                source_path=source_path,
                destination_path=f.name,
                ignore_output=True,
                ignore_items=ignore_items,
            )
            f_path = Path(f.name)
            sys.stdout.write(f_path.read_text())
        f_path.unlink()


def main(args):
    location = Path(__file__).parent.parent / "alembic"
    if args.file in {"all", "op"}:
        run_file(
            location / "op.pyi", Operations, args.stdout, IGNORE_ITEMS["op"]
        )
    if args.file in {"all", "context"}:
        run_file(
            location / "context.pyi",
            EnvironmentContext,
            args.stdout,
            IGNORE_ITEMS["context"],
        )


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument(
        "--file",
        choices={"op", "context", "all"},
        default="all",
        help="Which file to generate. Default is to regenerate all files",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write to stdout instead of saving to file",
    )
    args = parser.parse_args()
    main(args)
