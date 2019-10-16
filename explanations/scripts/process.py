import argparse
import logging
from typing import List

from scripts.compile_tex import CompileTex
from scripts.extract_equations import ExtractEquations
from scripts.fetch_arxiv_sources import FetchArxivSources
from scripts.unpack_sources import UnpackSources

command_classes: List = [FetchArxivSources, UnpackSources, ExtractEquations, CompileTex]


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Process arXiv papers.")
    parser.add_argument("-v", help="print debugging information", action="store_true")
    subparsers = parser.add_subparsers(help="data processing commands")

    for CommandClass in command_classes:
        command_parser = subparsers.add_parser(
            CommandClass.get_name(), help=CommandClass.get_description()
        )
        command_parser.set_defaults(command_class=CommandClass)
        CommandClass.init_parser(command_parser)

    args = parser.parse_args()
    if not hasattr(args, "command_class"):
        parser.print_help()
        raise SystemExit

    if args.v:
        logging.basicConfig(level=logging.DEBUG)

    CommandClass = args.command_class
    command = CommandClass(args)
    for item in command.load():
        for result in command.process(item):
            command.save(item, result)