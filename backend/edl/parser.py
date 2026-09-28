"""Recursive descent parser producing the AST in :mod:`edl.ast_nodes`."""

from __future__ import annotations

from . import ast_nodes as ast
from .lexer import Token, tokenize


class ParseEduError(Exception):
    pass


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def next(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, kind: str, value: object = None) -> Token:
        tok = self.peek()
        if tok.kind != kind or (value is not None and tok.value != value):
            wanted = value if value is not None else kind
            raise ParseEduError(f"line {tok.line}: expected {wanted!r} but found {tok.value!r}")
        return self.next()

    def skip_newlines(self) -> None:
        while self.peek().kind == "NEWLINE":
            self.next()

    def parse_program(self) -> ast.Program:
        program = ast.Program()
        self.skip_newlines()
        while self.peek().kind != "EOF":
            tok = self.peek()
            if tok.kind == "KEYWORD" and tok.value == "fun":
                fn = self.parse_function()
                if fn.name in program.functions:
                    raise ParseEduError(f"line {fn.line}: function {fn.name!r} is defined more than once")
                program.functions[fn.name] = fn
            else:
                raise ParseEduError(f"line {tok.line}: top level may only contain fun declarations")
            self.skip_newlines()
        if "main" not in program.functions:
            raise ParseEduError("program must define a main() function")
        program.entry_line = program.functions["main"].line
        return program

    def parse_function(self) -> ast.Function:
        header = self.expect("KEYWORD", "fun")
        name_tok = self.expect("NAME")
        self.expect("OP", "(")
        params: list[str] = []
        if not (self.peek().kind == "OP" and self.peek().value == ")"):
            while True:
                param = self.expect("NAME")
                params.append(param.value)
                if self.peek().kind == "OP" and self.peek().value == ",":
                    self.next()
                    continue
                break
        self.expect("OP", ")")
        self.expect("NEWLINE")
        body = self.parse_block()
        if not body:
            raise ParseEduError(f"line {header.line}: function {name_tok.value!r} has an empty body")
        return ast.Function(name_tok.value, params, body, header.line)

    def parse_block(self) -> list:
        self.expect("INDENT")
        statements = []
        while self.peek().kind not in ("DEDENT", "EOF"):
            statements.append(self.parse_statement())
            while self.peek().kind == "NEWLINE":
                self.next()
        self.expect("DEDENT")
        return statements

    def parse_statement(self):
        tok = self.peek()
        if tok.kind == "KEYWORD":
            if tok.value == "if":
                return self.parse_if()
            if tok.value == "while":
                return self.parse_while()
            if tok.value == "return":
                return self.parse_return()
        # Assignment has the shape NAME '=' expression (but not '==').
        if tok.kind == "NAME" and self.peek(1).kind == "OP" and self.peek(1).value == "=":
            target = self.next()
            self.next()
            value = self.parse_expression()
            self.expect("NEWLINE")
            return ast.Assign(target.value, value, tok.line)
        expr = self.parse_expression()
        self.expect("NEWLINE")
        return ast.ExprStmt(expr, expr.line)

    def parse_if(self) -> ast.If:
        header = self.next()
        condition = self.parse_expression()
        self.expect("NEWLINE")
        body = self.parse_block()
        else_body = None
        self.skip_newlines()
        if self.peek().kind == "KEYWORD" and self.peek().value == "else":
            self.next()
            self.expect("NEWLINE")
            else_body = self.parse_block()
        return ast.If(condition, body, else_body, header.line)

    def parse_while(self) -> ast.While:
        header = self.next()
        condition = self.parse_expression()
        self.expect("NEWLINE")
        body = self.parse_block()
        return ast.While(condition, body, header.line)

    def parse_return(self) -> ast.Return:
        header = self.next()
        value = None
        if self.peek().kind != "NEWLINE":
            value = self.parse_expression()
        self.expect("NEWLINE")
        return ast.Return(value, header.line)

    # Expressions: or, and, not/comparison, additive, multiplicative, unary, primary.
    def parse_expression(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.peek().kind == "KEYWORD" and self.peek().value == "or":
            op_tok = self.next()
            right = self.parse_and()
            left = ast.Binary("or", left, right, op_tok.line)
        return left

    def parse_and(self):
        left = self.parse_comparison()
        while self.peek().kind == "KEYWORD" and self.peek().value == "and":
            op_tok = self.next()
            right = self.parse_comparison()
            left = ast.Binary("and", left, right, op_tok.line)
        return left

    def parse_comparison(self):
        left = self.parse_addition()
        while self.peek().kind == "OP" and self.peek().value in ("==", "!=", "<", "<=", ">", ">="):
            op_tok = self.next()
            right = self.parse_addition()
            left = ast.Binary(op_tok.value, left, right, op_tok.line)
        return left

    def parse_addition(self):
        left = self.parse_multiplication()
        while self.peek().kind == "OP" and self.peek().value in ("+", "-"):
            op_tok = self.next()
            right = self.parse_multiplication()
            left = ast.Binary(op_tok.value, left, right, op_tok.line)
        return left

    def parse_multiplication(self):
        left = self.parse_unary()
        while self.peek().kind == "OP" and self.peek().value in ("*", "/", "%"):
            op_tok = self.next()
            right = self.parse_unary()
            left = ast.Binary(op_tok.value, left, right, op_tok.line)
        return left

    def parse_unary(self):
        tok = self.peek()
        if tok.kind == "OP" and tok.value in ("-", "+"):
            self.next()
            return ast.Unary(tok.value, self.parse_unary(), tok.line)
        if tok.kind == "KEYWORD" and tok.value == "not":
            self.next()
            return ast.Unary("not", self.parse_unary(), tok.line)
        return self.parse_call()

    def parse_call(self):
        tok = self.peek()
        if tok.kind == "NAME" and self.peek(1).kind == "OP" and self.peek(1).value == "(":
            self.next()
            self.next()
            args = []
            if not (self.peek().kind == "OP" and self.peek().value == ")"):
                while True:
                    args.append(self.parse_expression())
                    if self.peek().kind == "OP" and self.peek().value == ",":
                        self.next()
                        continue
                    break
            self.expect("OP", ")")
            return ast.Call(tok.value, args, tok.line)
        return self.parse_primary()

    def parse_primary(self):
        tok = self.peek()
        if tok.kind == "NUMBER":
            self.next()
            return ast.Number(tok.value, tok.line)
        if tok.kind == "STRING":
            self.next()
            return ast.String(tok.value, tok.line)
        if tok.kind == "KEYWORD" and tok.value in ("true", "false"):
            self.next()
            return ast.Bool(tok.value == "true", tok.line)
        if tok.kind == "KEYWORD" and tok.value == "none":
            self.next()
            return ast.NoneConst(tok.line)
        if tok.kind == "OP" and tok.value == "(":
            self.next()
            expr = self.parse_expression()
            self.expect("OP", ")")
            return expr
        if tok.kind == "NAME":
            self.next()
            return ast.Name(tok.value, tok.line)
        raise ParseEduError(f"line {tok.line}: unexpected token {tok.value!r}")


def parse_program(source: str) -> ast.Program:
    return Parser(tokenize(source)).parse_program()
