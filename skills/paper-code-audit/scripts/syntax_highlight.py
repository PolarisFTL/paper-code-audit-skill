"""Small, offline source highlighter; never imports or executes audited code.

``highlight_lines`` accepts a complete file as individual strings, with or
without line endings.  It returns one escaped HTML fragment per input string.
Token offsets only select slices of the originals: stripping our spans and
decoding HTML entities recovers every original character, including whitespace.

Python uses the standard library tokenizer over the complete file.  The other
languages use deliberately conservative lexical rules, not an AST or a type
checker.  In particular, call/class colors describe spelling and nearby tokens,
not resolved symbols; JS template interpolation is colored as a string.
"""
from __future__ import annotations

import builtins
import html
import io
import keyword
import re
import token
import tokenize


_PY_BUILTINS = frozenset(dir(builtins))
_C_KEYWORDS = frozenset("""
alignas alignof asm auto bool break case catch char char8_t char16_t char32_t
class concept const consteval constexpr constinit const_cast continue co_await
co_return co_yield decltype default delete do double dynamic_cast else enum
explicit export extern false float for friend goto if inline int long mutable
namespace new noexcept nullptr operator private protected public register
reinterpret_cast requires return short signed sizeof static static_assert
static_cast struct switch template this thread_local throw true try typedef
typeid typename union unsigned using virtual void volatile wchar_t while
_Alignas _Alignof _Atomic _Bool _Complex _Generic _Imaginary _Noreturn
_Static_assert _Thread_local restrict and and_eq bitand bitor compl not not_eq
or or_eq xor xor_eq
""".split())
_JS_KEYWORDS = frozenset("""
abstract any as assert asserts async await bigint boolean break case catch class
const constructor continue debugger declare default delete do else enum export
extends false finally for from function get global if implements import in infer
instanceof interface intrinsic is keyof let module namespace never new null
number object of override package private protected public readonly require
return satisfies set static string super switch symbol this throw true try type
typeof undefined unique unknown using var void while with yield
""".split())
_JS_BUILTINS = frozenset("""
Array ArrayBuffer BigInt Boolean Date Error EvalError Float32Array Float64Array
Function Infinity Int8Array Int16Array Int32Array Intl JSON Map Math NaN Number
Object Promise Proxy RangeError ReferenceError Reflect RegExp Set String Symbol
SyntaxError TypeError Uint8Array Uint16Array Uint32Array URIError WeakMap WeakSet
console decodeURI decodeURIComponent encodeURI encodeURIComponent eval fetch
isFinite isNaN parseFloat parseInt queueMicrotask setInterval setTimeout
""".split())
_SHELL_KEYWORDS = frozenset("""
if then else elif fi for while until do done case esac in function select time
coproc
""".split())
_SHELL_BUILTINS = frozenset("""
alias bg bind break builtin cd command compgen complete continue declare dirs
disown echo enable eval exec exit export false fc fg getopts hash help history
jobs kill let local logout mapfile popd printf pushd pwd read readarray readonly
return set shift shopt source test times trap true type typeset ulimit umask
unalias unset wait
""".split())

_IDENTIFIER = r"[^\W\d]\w*"
_NUMBER = (r"(?<![\w.])(?:0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|"
           r"(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?[\d_]+)?)"
           r"(?:[uUlLfFnN]+)?(?!\w)")
_C_STRING = (r"(?:u8|u|U|L)?(?:\"(?:\\[\s\S]|[^\"\\\r\n])*"
             r"(?:\"|(?=\r?$))|'(?:\\[\s\S]|[^'\\\r\n])*(?:'|(?=\r?$)))")
_C_LEXER = re.compile(
    r"(?P<comment>//[^\r\n]*|/\*[\s\S]*?(?:\*/|\Z))"
    r"|(?P<raw>(?:u8|u|U|L)?R\"(?P<delimiter>[^ ()\\\t\r\n]{0,16})"
    r"\([\s\S]*?\)(?P=delimiter)\")"
    r"|(?P<string>" + _C_STRING + r"|`(?:\\[\s\S]|[^`\\])*(?:`|\Z))"
    r"|(?P<number>" + _NUMBER + r")"
    r"|(?P<directive>^[ \t]*\#[ \t]*[a-zA-Z_]\w*)"
    r"|(?P<name>" + _IDENTIFIER + r")"
    r"|(?P<operator>=>|===|!==|\*\*=|\*\*|>>>?=?|<<=?|&&|\|\||\?\?|"
    r"\?\.|::|->|\.\.\.|[+*/%=<>!&|^~?:.,;()\[\]{}@-])", re.MULTILINE)
_JSON_LEXER = re.compile(
    r"(?P<comment>//[^\r\n]*|/\*[\s\S]*?(?:\*/|\Z))"
    r'|(?P<string>"(?:\\[^\r\n]|[^"\\\r\n])*(?:"|(?=\r?$)))'
    r"|(?P<number>-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)(?!\w)"
    r"|(?P<keyword>\b(?:true|false|null)\b)"
    r"|(?P<operator>[{}\[\]:,])", re.MULTILINE)
_YAML_LEXER = re.compile(
    r"(?P<comment>(?<!\S)\#[^\r\n]*)"
    r'|(?P<string>(?<!\w)"(?:\\[\s\S]|[^"\\])*(?:"|\Z)'
    r"|(?<!\w)'(?:''|[^'])*(?:'|\Z))"
    r"|(?P<block>(?<!\S)[|>](?:[+-]?[1-9]?|[1-9][+-]?)(?=[ \t]*(?:\#[^\r\n]*)?\r?$))"
    r"|(?:^[ \t]*(?:-[ \t]+)*|(?<=[{\[,])[ \t]*)(?P<property>[\w][\w .-]*)(?=:[ \t\r\n])"
    r"|(?P<decorator>[&*][\w.-]+|!!?[\w:/.-]+)"
    r"|(?P<number>" + _NUMBER + r")"
    r"|(?P<keyword>\b(?:true|false|null|yes|no|on|off)\b|~(?=\s|[,}\]]|\Z))"
    r"|(?P<operator>[\[\]{},:?|-]|>)", re.MULTILINE | re.IGNORECASE)
_SHELL_LEXER = re.compile(
    r"(?P<comment>(?<![^\s;|&()])\#[^\r\n]*)"
    r'|(?P<string>"(?:\\[\s\S]|[^"\\])*(?:"|\Z)'
    r"|'[^']*(?:'|\Z)|`(?:\\[\s\S]|[^`\\])*(?:`|\Z)|\\[\s\S])"
    r"|(?P<heredoc><<-?[ \t]*(?P<hdquote>['\"]?)(?P<hddelim>[a-zA-Z_]\w*)(?P=hdquote))"
    r"|(?P<builtin>\$\{[^}\r\n]*\}|\$\([^\r\n]*?\)|\$(?:[a-zA-Z_]\w*|[0-9@*#?$!_-]))"
    r"|(?P<number>" + _NUMBER + r")"
    r"|(?P<name>" + _IDENTIFIER + r")"
    r"|(?P<operator>&&|\|\||;;|>>|<<|[;&|(){}<>!=+*/%-])", re.MULTILINE)
_JSON_PROPERTY_END = re.compile(r"\s*:")
_YAML_PROPERTY_END = re.compile(r"[ \t]*:(?:\s|$)")
_CALL_PAREN = re.compile(r"[ \t]*\(")


def _escape(text):
    # Literal CR/CRLF is normalized by browser HTML parsers.  A character
    # reference preserves the original CR in textContent as well as round trips.
    return html.escape(text, quote=True).replace("\r", "&#13;")


def _layout(lines):
    """Give lexical text and original-item ranges in one shared coordinate space."""
    parts, ranges, offset = [], [], 0
    for line in lines:
        # A lone CR is a logical newline, with the same length as the original.
        lexical = re.sub(r"\r(?!\n)", "\n", line)
        ranges.append((offset, offset + len(line)))
        parts.append(lexical)
        offset += len(line)
        if not lexical.endswith("\n"):
            parts.append("\n")
            offset += 1
    return "".join(parts), ranges


def _python_tokens(source):
    row_starts = [0] + [m.end() for m in re.finditer("\n", source)]
    tokens = []
    try:
        for item in tokenize.generate_tokens(io.StringIO(source).readline):
            tokens.append(item)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Incomplete/invalid source is evidence too.  Keep valid prefix tokens;
        # the unrecognized remainder is rendered as escaped plain text.
        pass
    ignored = {tokenize.ENCODING, token.INDENT, token.DEDENT, token.NEWLINE,
               tokenize.NL, token.ENDMARKER, token.COMMENT}
    significant = [i for i, item in enumerate(tokens) if item.type not in ignored]
    neighbors = {index: (significant[n - 1] if n else None,
                         significant[n + 1] if n + 1 < len(significant) else None)
                 for n, index in enumerate(significant)}
    decorators = set()
    at_line_start, in_decorator = True, False
    for index, item in enumerate(tokens):
        if item.type == token.NEWLINE:
            at_line_start, in_decorator = True, False
        elif item.type not in ignored:
            if at_line_start and item.type == token.OP and item.string == "@":
                in_decorator = True
            if in_decorator:
                if item.type == token.OP and item.string == "(":
                    in_decorator = False
                elif item.type == token.NAME or item.string in {"@", "."}:
                    decorators.add(index)
            at_line_start = False

    def absolute(position):
        row, column = position
        if row < 1 or row > len(row_starts):
            return len(source)
        return min(row_starts[row - 1] + column, len(source))

    result = []
    for index, item in enumerate(tokens):
        kind = None
        before, after = neighbors.get(index, (None, None))
        previous = tokens[before].string if before is not None else ""
        following = tokens[after].string if after is not None else ""
        if item.type == token.COMMENT:
            kind = "comment"
        elif item.type == token.STRING or token.tok_name.get(item.type, "").startswith("FSTRING"):
            kind = "string"
        elif item.type == token.NUMBER:
            kind = "number"
        elif index in decorators:
            kind = "decorator"
        elif item.type == token.NAME:
            if keyword.iskeyword(item.string):
                kind = "keyword"
            elif previous == "class":
                kind = "class"
            elif previous == "def":
                kind = "function"
            elif item.string in _PY_BUILTINS and previous != ".":
                kind = "builtin"
            elif following == "(":
                kind = "function"
        elif item.type == token.OP:
            kind = "operator"
        if kind:
            result.append((absolute(item.start), absolute(item.end), kind))
    return result


def _c_tokens(source, javascript=False):
    matches = list(_C_LEXER.finditer(source))
    keywords = _JS_KEYWORDS if javascript else _C_KEYWORDS
    previous = ""
    for index, match in enumerate(matches):
        kind, value = match.lastgroup, match.group()
        if kind == "name":
            if value in keywords:
                kind = "keyword"
            elif previous in {"class", "struct", "interface", "enum", "namespace"}:
                kind = "class"
            elif previous == "function":
                kind = "function"
            elif javascript and value in _JS_BUILTINS and previous not in {".", "?."}:
                kind = "builtin"
            else:
                following = index + 1
                while following < len(matches) and matches[following].lastgroup == "comment":
                    following += 1
                kind = "function" if following < len(matches) and matches[following].group() == "(" else None
        elif kind == "raw":
            kind = "string"
        elif kind == "directive":
            kind = "keyword"
        if kind:
            yield match.start(), match.end(), kind
        if match.lastgroup != "comment":
            previous = value


def _json_tokens(source):
    for match in _JSON_LEXER.finditer(source):
        kind = match.lastgroup
        if kind == "string" and _JSON_PROPERTY_END.match(source, match.end()):
            kind = "property"
        yield match.start(), match.end(), kind


def _yaml_tokens(source):
    cursor = 0
    while True:
        match = _YAML_LEXER.search(source, cursor)
        if match is None:
            break
        kind = match.lastgroup
        if kind != "block":
            if kind == "string" and _YAML_PROPERTY_END.match(source, match.end()):
                kind = "property"
            # The plain-key rule also consumes its structural indentation;
            # color only the key, keeping the prefix as untouched source.
            start = match.start("property") if match.lastgroup == "property" else match.start()
            yield start, match.end(), kind
            cursor = match.end()
            continue
        yield match.start(), match.end(), "operator"
        line_start = source.rfind("\n", 0, match.start()) + 1
        prefix = source[line_start:match.start()]
        indent = len(prefix) - len(prefix.lstrip(" \t"))
        line_end = source.find("\n", match.end())
        line_end = len(source) if line_end < 0 else line_end + 1
        comment = source.find("#", match.end(), line_end)
        if comment >= 0:
            yield comment, source.find("\n", comment) if "\n" in source[comment:line_end] else line_end, "comment"
        body_start, cursor = line_end, line_end
        while cursor < len(source):
            next_end = source.find("\n", cursor)
            next_end = len(source) if next_end < 0 else next_end + 1
            body_line = source[cursor:next_end]
            body_indent = len(body_line) - len(body_line.lstrip(" \t"))
            if body_line.strip() and body_indent <= indent:
                break
            cursor = next_end
        if cursor > body_start:
            yield body_start, cursor, "string"


def _shell_tokens(source):
    cursor, previous = 0, ""
    while True:
        match = _SHELL_LEXER.search(source, cursor)
        if match is None:
            break
        kind, value = match.lastgroup, match.group()
        cursor = match.end()
        if kind == "name":
            if value in _SHELL_KEYWORDS:
                kind = "keyword"
            elif previous == "function" or _CALL_PAREN.match(source, cursor):
                kind = "function"
            else:
                kind = "builtin" if value in _SHELL_BUILTINS else None
        if kind == "heredoc":
            yield match.start(), match.end(), "operator"
            header_end = source.find("\n", cursor)
            if header_end < 0:
                continue
            # Tokenize the remainder of the command before its here-document.
            for start, end, subkind in _shell_tokens(source[cursor:header_end]):
                yield cursor + start, cursor + end, subkind
            delimiter = re.escape(match.group("hddelim"))
            tabs = r"\t*" if value.startswith("<<-") else ""
            ending = re.compile(r"^" + tabs + delimiter + r"\r?$", re.MULTILINE).search(source, header_end + 1)
            end = ending.end() if ending else len(source)
            yield header_end + 1, end, "string"
            cursor = end
        elif kind:
            yield match.start(), match.end(), kind
        if match.lastgroup != "comment":
            previous = value


def _render(lines, ranges, spans):
    result, index = [], 0
    for line, (start, end) in zip(lines, ranges):
        while index < len(spans) and spans[index][1] <= start:
            index += 1
        pieces, cursor, current = [], 0, index
        while current < len(spans) and spans[current][0] < end:
            token_start, token_end, kind = spans[current]
            local_start = max(cursor, token_start - start, 0)
            local_end = min(token_end - start, len(line))
            if local_end > local_start:
                pieces.append(_escape(line[cursor:local_start]))
                pieces.append('<span class="tok-' + kind + '">' +
                              _escape(line[local_start:local_end]) + '</span>')
                cursor = local_end
            current += 1
        pieces.append(_escape(line[cursor:]))
        result.append("".join(pieces))
    return result


def highlight_lines(path: str, lines: list[str]) -> list[str]:
    """Escape and highlight a *complete* file, returning exactly one item per line.

    ``path`` only selects the language; it is never opened.  Unsupported suffixes
    are escaped as plain text.  No stylesheet, script, URL, or user-controlled
    attribute is emitted.  Callers supply CSS for the fixed ``tok-*`` classes.
    """
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1].lower()
    suffix = name.rsplit(".", 1)[-1] if "." in name else ""
    if suffix in {"py", "pyi", "pyw"}:
        lexer = _python_tokens
    elif suffix in {"json", "jsonc"}:
        lexer = _json_tokens
    elif suffix in {"yaml", "yml"}:
        lexer = _yaml_tokens
    elif suffix in {"c", "h", "cc", "hh", "cpp", "hpp", "cxx", "hxx", "cu", "cuh"}:
        lexer = _c_tokens
    elif suffix in {"js", "mjs", "cjs", "jsx", "ts", "mts", "cts", "tsx"}:
        lexer = lambda source: _c_tokens(source, javascript=True)
    elif suffix in {"sh", "bash", "zsh", "ksh"} or name in {".bashrc", ".bash_profile", ".zshrc", ".profile"}:
        lexer = _shell_tokens
    else:
        return [_escape(line) for line in lines]
    source, ranges = _layout(lines)
    return _render(lines, ranges, list(lexer(source)))
