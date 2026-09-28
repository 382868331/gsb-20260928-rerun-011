from __future__ import annotations

from .ast_nodes import (
    Assign,
    BinOp,
    BoolLit,
    Call,
    ExprStmt,
    FunDef,
    If,
    IntLit,
    Let,
    Program,
    Return,
    UnaryOp,
    Var,
    While,
)
from .lexer import Lexer, Token


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def _expect(self, kind: str, what: str | None = None) -> Token:
        tok = self._peek()
        if tok.kind != kind:
            label = what or kind
            raise ParseError(f"第 {tok.line} 行: 期望 {label}，但遇到 {tok.kind}")
        return self._advance()

    def parse(self) -> Program:
        program = Program(line=1)
        while self._peek().kind != "EOF":
            tok = self._peek()
            if tok.kind == "FUN":
                fn = self._parse_function()
                if fn.name in program.functions:
                    raise ParseError(f"第 {fn.line} 行: 函数 {fn.name} 重复定义")
                program.functions[fn.name] = fn
            else:
                program.main_body.append(self._parse_statement())
        return program

    def _parse_function(self) -> FunDef:
        fun_tok = self._advance()
        name_tok = self._expect("IDENT", "函数名")
        self._expect("(")
        params: list[str] = []
        if self._peek().kind != ")":
            while True:
                p = self._expect("IDENT", "参数名")
                params.append(p.value)
                if self._peek().kind == ",":
                    self._advance()
                    continue
                break
        self._expect(")")
        body = self._parse_block(fun_tok.line)
        return FunDef(line=fun_tok.line, name=name_tok.value, params=params, body=body)

    def _parse_block(self, line: int) -> list:
        self._expect("NEWLINE", "换行")
        self._expect("INDENT", "缩进的代码块")
        body = []
        while self._peek().kind not in ("DEDENT", "EOF"):
            body.append(self._parse_statement())
        self._expect("DEDENT")
        return body

    def _parse_statement(self):
        tok = self._peek()
        if tok.kind == "LET":
            self._advance()
            name = self._expect("IDENT", "变量名")
            self._expect("=", "'='")
            value = self._parse_expr()
            self._expect("NEWLINE", "换行")
            return Let(line=tok.line, name=name.value, value=value)
        if tok.kind == "IF":
            return self._parse_if()
        if tok.kind == "WHILE":
            return self._parse_while()
        if tok.kind == "RETURN":
            self._advance()
            value = None
            if self._peek().kind != "NEWLINE":
                value = self._parse_expr()
            self._expect("NEWLINE", "换行")
            return Return(line=tok.line, value=value)
        # Expression statement or assignment: IDENT '=' expr  /  call
        if tok.kind == "IDENT" and self._peek(1).kind == "=":
            self._advance()
            self._advance()
            value = self._parse_expr()
            self._expect("NEWLINE", "换行")
            return Assign(line=tok.line, name=tok.value, value=value)
        expr = self._parse_expr()
        self._expect("NEWLINE", "换行")
        return ExprStmt(line=tok.line, expr=expr)

    def _parse_if(self) -> If:
        tok = self._advance()
        cond = self._parse_expr()
        then_body = self._parse_block(tok.line)
        else_body: list = []
        if self._peek().kind == "ELSE":
            else_tok = self._advance()
            if self._peek().kind == "IF":
                else_body = [self._parse_if()]
            else:
                else_body = self._parse_block(else_tok.line)
        return If(line=tok.line, cond=cond, then_body=then_body, else_body=else_body)

    def _parse_while(self) -> While:
        tok = self._advance()
        cond = self._parse_expr()
        body = self._parse_block(tok.line)
        return While(line=tok.line, cond=cond, body=body)

    # ---- expressions ----

    def _parse_expr(self):
        return self._parse_or()

    def _parse_or(self):
        left = self._parse_and()
        while self._peek().kind == "OR":
            tok = self._advance()
            right = self._parse_and()
            left = BinOp(line=tok.line, op="or", left=left, right=right)
        return left

    def _parse_and(self):
        left = self._parse_not()
        while self._peek().kind == "AND":
            tok = self._advance()
            right = self._parse_not()
            left = BinOp(line=tok.line, op="and", left=left, right=right)
        return left

    def _parse_not(self):
        if self._peek().kind == "NOT":
            tok = self._advance()
            return UnaryOp(line=tok.line, op="not", operand=self._parse_not())
        return self._parse_comparison()

    _COMPARE_OPS = {"EQ", "NE", "<", ">", "LE", "GE"}

    def _parse_comparison(self):
        left = self._parse_additive()
        while self._peek().kind in self._COMPARE_OPS:
            tok = self._advance()
            right = self._parse_additive()
            left = BinOp(line=tok.line, op=tok.value, left=left, right=right)
        return left

    def _parse_additive(self):
        left = self._parse_multiplicative()
        while self._peek().kind in ("+", "-"):
            tok = self._advance()
            right = self._parse_multiplicative()
            left = BinOp(line=tok.line, op=tok.kind, left=left, right=right)
        return left

    def _parse_multiplicative(self):
        left = self._parse_unary()
        while self._peek().kind in ("*", "/", "%"):
            tok = self._advance()
            right = self._parse_unary()
            left = BinOp(line=tok.line, op=tok.kind, left=left, right=right)
        return left

    def _parse_unary(self):
        if self._peek().kind == "-":
            tok = self._advance()
            return UnaryOp(line=tok.line, op="-", operand=self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self):
        tok = self._peek()
        if tok.kind == "INT":
            self._advance()
            return IntLit(line=tok.line, value=tok.value)
        if tok.kind == "BOOL":
            self._advance()
            return BoolLit(line=tok.line, value=tok.value)
        if tok.kind == "IDENT":
            self._advance()
            if self._peek().kind == "(":
                self._advance()
                args = []
                if self._peek().kind != ")":
                    while True:
                        args.append(self._parse_expr())
                        if self._peek().kind == ",":
                            self._advance()
                            continue
                        break
                close = self._expect(")")
                return Call(line=tok.line, name=tok.value, args=args)
            return Var(line=tok.line, name=tok.value)
        if tok.kind == "(":
            self._advance()
            expr = self._parse_expr()
            self._expect(")")
            return expr
        raise ParseError(f"第 {tok.line} 行: 意外的记号 {tok.kind}")


def parse_source(source: str) -> Program:
    return Parser(Lexer().tokenize(source)).parse()
