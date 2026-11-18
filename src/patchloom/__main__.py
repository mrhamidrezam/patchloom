"""Zero-dependency command-line interface for patchloom."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from patchloom import (
    ConflictPolicy,
    Patch,
    PatchLoomError,
    Pointer,
    __version__,
    apply_merge_patch,
    create_merge_patch,
    diff,
    diff_reversible,
    merge3,
)
from patchloom._core import JsonValue


def _load_json_file(path_str: str) -> JsonValue:
    if path_str == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_str).read_text(encoding="utf-8")
    return json.loads(raw)  # type: ignore[no-any-return]


def _dump_json(value: object, *, indent: int | None, out: TextIO) -> None:
    effective_indent = None if (indent is not None and indent < 0) else indent
    out.write(json.dumps(value, indent=effective_indent, ensure_ascii=False) + "\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchloom",
        description="RFC 6902 JSON Patch, RFC 7396 Merge Patch, reversible diff & 3-way merge CLI.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_diff = sub.add_parser("diff", help="Compute RFC 6902 JSON Patch between two JSON files.")
    p_diff.add_argument("source", help="Source JSON file path (or '-' for stdin).")
    p_diff.add_argument("target", help="Target JSON file path.")
    p_diff.add_argument(
        "--reversible",
        action="store_true",
        help="Emit both 'forward' and 'reverse' RFC 6902 patches.",
    )
    p_diff.add_argument(
        "--no-moves",
        action="store_true",
        help="Disable move operation detection.",
    )
    p_diff.add_argument(
        "--copies",
        action="store_true",
        help="Enable structural subtree copy operation detection.",
    )
    p_diff.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Pointer or wildcard pattern to ignore (repeatable).",
    )
    p_diff.add_argument(
        "--array-key",
        default=None,
        metavar="KEY",
        help="Object identity key name for keyed array alignment (e.g. 'id').",
    )
    p_diff.add_argument("--indent", type=int, default=2, help="JSON output indentation (default: 2).")

    p_apply = sub.add_parser("apply", help="Apply an RFC 6902 JSON Patch atomically.")
    p_apply.add_argument("document", help="Target JSON document file path.")
    p_apply.add_argument("patch", help="RFC 6902 JSON Patch file path.")
    p_apply.add_argument("--indent", type=int, default=2, help="JSON output indentation (default: 2).")

    p_mp = sub.add_parser("merge-patch", help="Generate an RFC 7396 JSON Merge Patch.")
    p_mp.add_argument("source", help="Source JSON file path.")
    p_mp.add_argument("target", help="Target JSON file path.")
    p_mp.add_argument("--indent", type=int, default=2, help="JSON output indentation (default: 2).")

    p_amp = sub.add_parser("apply-merge", help="Apply an RFC 7396 JSON Merge Patch.")
    p_amp.add_argument("target", help="Target JSON document file path.")
    p_amp.add_argument("patch", help="RFC 7396 Merge Patch file path.")
    p_amp.add_argument("--indent", type=int, default=2, help="JSON output indentation (default: 2).")

    p_m3 = sub.add_parser("merge3", help="Three-way structural merge of base, ours, and theirs.")
    p_m3.add_argument("base", help="Common ancestor JSON file path.")
    p_m3.add_argument("ours", help="Local ('ours') JSON file path.")
    p_m3.add_argument("theirs", help="Remote ('theirs') JSON file path.")
    p_m3.add_argument(
        "--policy",
        choices=[p.value for p in ConflictPolicy],
        default=ConflictPolicy.RAISE.value,
        help="Conflict resolution policy (default: raise).",
    )
    p_m3.add_argument(
        "--array-key",
        default=None,
        metavar="KEY",
        help="Identity key name for keyed array three-way merges (e.g. 'id').",
    )
    p_m3.add_argument("--indent", type=int, default=2, help="JSON output indentation (default: 2).")

    p_get = sub.add_parser("get", help="Resolve an RFC 6901 JSON Pointer inside a document.")
    p_get.add_argument("document", help="JSON document file path.")
    p_get.add_argument("pointer", help="RFC 6901 JSON Pointer string (e.g. '/users/0/name').")
    p_get.add_argument("--indent", type=int, default=2, help="JSON output indentation (default: 2).")

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Execute the patchloom CLI and return an OS exit code."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "diff":
            src = _load_json_file(args.source)
            dst = _load_json_file(args.target)
            if args.reversible:
                rev = diff_reversible(
                    src,
                    dst,
                    detect_moves=not args.no_moves,
                    detect_copies=args.copies,
                    ignore=args.ignore,
                    array_keys=args.array_key,
                )
                _dump_json(rev.to_dict(), indent=args.indent, out=out)
            else:
                patch = diff(
                    src,
                    dst,
                    detect_moves=not args.no_moves,
                    detect_copies=args.copies,
                    ignore=args.ignore,
                    array_keys=args.array_key,
                )
                _dump_json(patch.to_list(), indent=args.indent, out=out)
            return 0

        if args.command == "apply":
            doc = _load_json_file(args.document)
            raw_patch = _load_json_file(args.patch)
            if not isinstance(raw_patch, list):
                err.write("error: patch file must contain a JSON array of RFC 6902 operations.\n")
                return 1
            result = Patch(raw_patch).apply(doc)  # type: ignore[arg-type]
            _dump_json(result, indent=args.indent, out=out)
            return 0

        if args.command == "merge-patch":
            src = _load_json_file(args.source)
            dst = _load_json_file(args.target)
            mp = create_merge_patch(src, dst)
            _dump_json(mp, indent=args.indent, out=out)
            return 0

        if args.command == "apply-merge":
            doc = _load_json_file(args.target)
            mp = _load_json_file(args.patch)
            result = apply_merge_patch(doc, mp)
            _dump_json(result, indent=args.indent, out=out)
            return 0

        if args.command == "merge3":
            base = _load_json_file(args.base)
            ours = _load_json_file(args.ours)
            theirs = _load_json_file(args.theirs)
            m3_res = merge3(
                base,
                ours,
                theirs,
                policy=ConflictPolicy(args.policy),
                array_keys=args.array_key,
            )
            _dump_json(m3_res.value, indent=args.indent, out=out)
            return 0

        if args.command == "get":
            doc = _load_json_file(args.document)
            val = Pointer.parse(args.pointer).resolve(doc)
            _dump_json(val, indent=args.indent, out=out)
            return 0

    except (PatchLoomError, ValueError, OSError, json.JSONDecodeError) as exc:
        err.write(f"error: {exc}\n")
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
