#!/usr/bin/env python3
"""erlancompiler — compilador bootstrap de Erlan v0.

Uso:
    erlancompiler.py archivo.e -o salida.c [--header salida.h]

Este compilador es desechable: existe para que el kernel pueda escribirse en Erlan
desde ya. Cuando el lenguaje sea suficiente, se reescribe en Erlan (self-hosting).

Subconjunto v0:
    tipos     i8 i16 i32 i64 u8 u16 u32 u64 f32 f64 bool void str
              ptr<T>  []T (slice)  [N]T (array)  struct  enum
    decls     fn  struct  enum  const  extern c { ... }  extern c "hdr.h" { ... }
    stmts     var/let  asignación (= += -= *= /= %= &= |= ^= <<= >>=)
              if/else  while  loop  for i in a..b  break  continue  return
              defer  unsafe { }  match  llamada
    exprs     literales, operadores C, cast `as`, &x, *p, a[i], a[lo..hi],
              s.len, e.campo, Enum.Variante, Struct { campo: v }
Seguridad: índices comprobados en slices y arrays; aritmética de punteros,
indexación de punteros y casts de punteros sólo dentro de `unsafe`.

Modelo de memoria (decisión 2026-10-26: el de Rust, primera rebanada; ver
docs/ERLAN-MEMORIA.md):
    tipos     &T (referencia compartida)  &mut T (referencia exclusiva)
    decls     @copy struct ...  (el struct se copia; si no, se mueve)
              @drop fn f(x: &mut T) { ... }  (destructor de T, corre al salir del ámbito)
    exprs     &lugar  &mut lugar
    análisis  IR de flujo de control (equivalente al MIR) por función; sobre ella,
              comprobación de movimientos (uso tras mover, mover dos veces, movimiento
              condicional y en bucles) y de préstamos con vida por flujo (NLL).

Programación de sistemas (para que el kernel no necesite C, ver docs/ERLAN-SISTEMA.md):
    import modulo;                 usa las funciones, structs, enums y consts de modulo.e
                                   (se busca junto al archivo y en los -I; incluye modulo.h)
    asm("plantilla" : salidas : entradas : clobbers);   sólo dentro de unsafe; sintaxis GNU
    asm("..."); a nivel de módulo   código ensamblador global (stubs, _start)
    fn f(fmt: str, ...) { vararg_start(); vararg_u64() ... vararg_end(); }   funciones variádicas
    @noreturn fn  @section("s") @used @volatile var   atributos de enlace
    [a, b, c]                      literal de array (sólo como inicializador)
    ptr<void>                      puntero crudo sin tipo (ABI de C)
    extern c "x.h" { let tabla: [N]u8; }   datos definidos en C
    f as u64                       dirección de una función
"""
import sys, re, argparse

# --------------------------------------------------------------------------- errores
class ErlanError(Exception):
    def __init__(self, msg, line=0, col=0):
        super().__init__(msg); self.msg, self.line, self.col = msg, line, col

# --------------------------------------------------------------------------- lexer
KEYWORDS = {'fn','var','let','const','if','else','while','for','in','loop','break','continue',
            'return','defer','unsafe','extern','struct','enum','match','as','true','false','import','mut','asm'}
INT_TYPES = ['i8','i16','i32','i64','u8','u16','u32','u64']
FLOAT_TYPES = ['f32','f64']

TOKEN_RE = re.compile(r'''
    (?P<ws>[ \t\r]+) | (?P<nl>\n) | (?P<comment>//[^\n]*) | (?P<bcomment>/\*.*?\*/) |
    (?P<float>\d+\.\d+(?:[eE][+-]?\d+)?) | (?P<int>0x[0-9a-fA-F_]+|0b[01_]+|\d[\d_]*) |
    (?P<str>"(?:\\.|[^"\\])*") | (?P<char>'(?:\\.|[^'\\])') |
    (?P<ident>[A-Za-z_][A-Za-z0-9_]*) |
    (?P<op>\.\.=|\.\.\.|\.\.|->|=>|<<=|>>=|<<|>>|<=|>=|==|!=|&&|\|\||\+=|-=|\*=|/=|%=|&=|\|=|\^=|[-+*/%&|^!~<>=(){}\[\];:,.?@])
''', re.X | re.S)

class Tok:
    __slots__ = ('kind','val','line','col')
    def __init__(self, kind, val, line, col): self.kind, self.val, self.line, self.col = kind, val, line, col
    def __repr__(self): return f'{self.kind}:{self.val!r}@{self.line}:{self.col}'

def lex(src):
    toks, pos, line, lstart = [], 0, 1, 0
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m: raise ErlanError(f'carácter inesperado {src[pos]!r}', line, pos - lstart + 1)
        kind = m.lastgroup; text = m.group(kind); col = pos - lstart + 1
        if kind == 'nl': line += 1; lstart = m.end()
        elif kind == 'bcomment':
            n = text.count('\n')
            if n: line += n; lstart = m.start() + text.rfind('\n') + 1
        elif kind in ('ws','comment'): pass
        elif kind == 'ident':
            toks.append(Tok('kw' if text in KEYWORDS else 'ident', text, line, col))
        elif kind == 'int': toks.append(Tok('int', text.replace('_',''), line, col))
        elif kind == 'float': toks.append(Tok('float', text, line, col))
        elif kind == 'str': toks.append(Tok('str', text, line, col))
        elif kind == 'char': toks.append(Tok('char', text, line, col))
        else: toks.append(Tok('op', text, line, col))
        pos = m.end()
    toks.append(Tok('eof', '', line, pos - lstart + 1))
    return toks

# --------------------------------------------------------------------------- AST
class Node:
    def __init__(self, kind, **kw):
        self.kind = kind; self.__dict__.update(kw)
    def __repr__(self): return f'<{self.kind} {self.__dict__}>'

# tipos: tuplas
T_BOOL, T_VOID, T_STR = ('bool',), ('void',), ('str',)
T_LITINT, T_LITFLOAT = ('lit_int',), ('lit_float',)
def T_INT(n): return ('int', n)
def T_FLOAT(n): return ('float', n)
def T_PTR(t): return ('ptr', t)
def T_SLICE(t): return ('slice', t)
def T_ARRAY(n, t): return ('array', n, t)
def T_STRUCT(n): return ('struct', n)
def T_ENUM(n): return ('enum', n)
def T_REF(t, mut): return ('ref', t, mut)      # &T / &mut T (modelo de Rust)

def is_int(t): return t[0] in ('int','lit_int')
def is_float(t): return t[0] in ('float','lit_float')
def is_num(t): return is_int(t) or is_float(t)
def is_lit(t): return t[0] in ('lit_int','lit_float')

def tstr(t):
    k = t[0]
    if k in ('int','float'): return t[1]
    if k == 'bool': return 'bool'
    if k == 'void': return 'void'
    if k == 'str': return 'str'
    if k == 'lit_int': return '{integer}'
    if k == 'lit_float': return '{float}'
    if k == 'ptr': return f'ptr<{tstr(t[1])}>'
    if k == 'ref': return ('&mut ' if t[2] else '&') + tstr(t[1])
    if k == 'slice': return f'[]{tstr(t[1])}'
    if k == 'array': return f'[{t[1]}]{tstr(t[2])}'
    if k in ('struct','enum'): return t[1]
    return str(t)

# --------------------------------------------------------------------------- módulos
class Modules:
    """Resuelve `import nombre;`: busca nombre.e junto al archivo que importa y en los -I,
    lo analiza una sola vez y lo guarda (parser y emisor comparten el resultado)."""
    def __init__(self, incdirs=()):
        self.incdirs = list(incdirs); self.cache = {}; self.loading = set()

    def find(self, name, base_path, n):
        import os
        dirs = ([os.path.dirname(os.path.abspath(base_path))] if base_path else []) + self.incdirs
        for d in dirs:
            cand = os.path.join(d, name + '.e')
            if os.path.isfile(cand): return cand
        raise ErlanError(f'import {name}: no se encuentra {name}.e (se busca junto al archivo y en los -I)', n.line, n.col)

    def load(self, name, base_path, n):
        if name in self.cache: return self.cache[name]
        path = self.find(name, base_path, n)
        if name in self.loading: return (path, None)     # ciclo: el otro módulo ya se está analizando
        self.loading.add(name)
        try:
            try:
                prog = Parser(lex(open(path, encoding='utf-8').read()), self, path).parse_program()
            except ErlanError as e:
                raise ErlanError(f'en {path}:{e.line}:{e.col}: {e.msg}', n.line, n.col)
        finally:
            self.loading.discard(name)
        self.cache[name] = (path, prog)
        return self.cache[name]

# --------------------------------------------------------------------------- parser
class Parser:
    def __init__(self, toks, modules=None, path=None):
        self.toks, self.i = toks, 0
        self.modules, self.path = modules, path
        self.struct_names = set()   # para desambiguar literales de struct
        self.enum_names = set()
        self.no_struct_lit = 0
        # pre-escaneo de nombres de struct/enum
        for j, t in enumerate(toks[:-1]):
            if t.kind == 'kw' and t.val in ('struct','enum') and toks[j+1].kind == 'ident':
                (self.struct_names if t.val == 'struct' else self.enum_names).add(toks[j+1].val)
        # y de los módulos importados (sus structs se usan en literales y tipos)
        for j, t in enumerate(toks[:-2]):
            if t.kind == 'kw' and t.val == 'import' and toks[j+1].kind == 'ident' and toks[j+2].kind == 'op' and toks[j+2].val == ';':
                if self.modules is None: continue
                _, prog = self.modules.load(toks[j+1].val, self.path, toks[j+1])
                if prog is not None:
                    self.struct_names |= prog.struct_names; self.enum_names |= prog.enum_names

    def peek(self, k=0): return self.toks[self.i + k]
    def next(self): t = self.toks[self.i]; self.i += 1; return t
    def at(self, kind, val=None):
        t = self.peek(); return t.kind == kind and (val is None or t.val == val)
    def at_op(self, v): return self.at('op', v)
    def at_kw(self, v): return self.at('kw', v)
    def accept(self, kind, val=None):
        if self.at(kind, val): return self.next()
        return None
    def expect(self, kind, val=None):
        t = self.peek()
        if not self.at(kind, val):
            want = val if val is not None else kind
            raise ErlanError(f'se esperaba {want!r}, se encontró {t.val!r}', t.line, t.col)
        return self.next()
    def err(self, msg, t=None):
        t = t or self.peek(); raise ErlanError(msg, t.line, t.col)

    # ---- programa
    def parse_program(self):
        decls = []
        while not self.at('eof'):
            decls.append(self.parse_decl())
        return Node('program', decls=decls, struct_names=set(self.struct_names), enum_names=set(self.enum_names))

    ATTRS = {'copy': ('struct',), 'drop': ('fn',), 'noreturn': ('fn',), 'section': ('fn', 'gvar'),
             'used': ('fn', 'gvar'), 'volatile': ('gvar',)}

    def parse_decl(self):
        t = self.peek()
        if self.at_op('@'):
            # atributos: @copy struct, @drop fn, @noreturn fn, @section("s") @used @volatile var
            attrs = {}
            while self.accept('op', '@'):
                a = self.expect('ident')
                if a.val not in self.ATTRS: self.err(f'atributo desconocido @{a.val}', a)
                arg = None
                if self.accept('op', '('):
                    arg = self.expect('str').val[1:-1]; self.expect('op', ')')
                if (a.val == 'section') != (arg is not None): self.err(f'@{a.val}: argumento inesperado o faltante', a)
                attrs[a.val] = arg
            d = self.parse_decl()
            for a in attrs:
                if d.kind not in self.ATTRS[a]: self.err(f'@{a} sólo se aplica a {"/".join(self.ATTRS[a])}', t)
            d.attrs = attrs
            return d
        if self.at_kw('asm'):
            # ensamblador a nivel de módulo: asm("..." "..."); -> __asm__("...");
            self.next(); self.expect('op','(')
            parts = []
            while self.at('str'): parts.append(self.next().val)
            self.expect('op',')'); self.expect('op',';')
            if not parts: self.err('asm de módulo requiere al menos una cadena', t)
            return Node('gasm', parts=parts, line=t.line, col=t.col)
        if self.at_kw('fn'): return self.parse_fn()
        if self.at_kw('struct'): return self.parse_struct()
        if self.at_kw('enum'): return self.parse_enum()
        if self.at_kw('const'): return self.parse_const()
        if self.at_kw('var') or self.at_kw('let'):
            st = self.parse_stmt(); st.kind = 'gvar'; st.attrs = {}; return st
        if self.at_kw('extern'): return self.parse_extern()
        if self.at_kw('import'): return self.parse_import()
        self.err(f'declaración inesperada {t.val!r}')

    def parse_import(self):
        t = self.expect('kw','import')
        if self.at('ident', 'c') and self.peek(1).kind == 'str':
            self.next(); hdr = self.expect('str'); self.expect('op',';')
            return Node('import_c', header=hdr.val[1:-1], line=t.line)
        name = self.expect('ident').val; self.expect('op',';')
        return Node('import', name=name, line=t.line, col=t.col)

    def parse_extern(self):
        t = self.expect('kw','extern'); self.expect('ident','c')
        header = None
        if self.at('str'): header = self.next().val[1:-1]
        self.expect('op','{')
        fns, structs, vars_ = [], [], []
        while not self.at_op('}'):
            if self.at_kw('struct'): structs.append(self.parse_struct())   # struct definido en otro módulo
            elif self.at_kw('let') or self.at_kw('var'):
                # datos definidos en C (tablas generadas): let tabla: [N]u8;
                vt = self.next(); vn = self.expect('ident').val; self.expect('op',':'); vty = self.parse_type(); self.expect('op',';')
                vars_.append(Node('extern_var', name=vn, ty=vty, mut=(vt.val == 'var'), line=vt.line, col=vt.col))
            else: fns.append(self.parse_fn(proto_only=True))
        self.expect('op','}')
        return Node('extern_c', header=header, fns=fns, structs=structs, vars=vars_, line=t.line)

    def parse_fn(self, proto_only=False):
        t = self.expect('kw','fn'); name = self.expect('ident').val
        self.expect('op','(')
        params, variadic = [], False
        while not self.at_op(')'):
            if self.accept('op','...'): variadic = True; break
            pn = self.expect('ident').val; self.expect('op',':'); pt = self.parse_type()
            params.append((pn, pt))
            if not self.accept('op',','): break
        self.expect('op',')')
        ret = T_VOID
        if self.accept('op','->'): ret = self.parse_type()
        if proto_only or self.at_op(';'):
            self.expect('op',';')
            return Node('fn', name=name, params=params, ret=ret, body=None, variadic=variadic, line=t.line, col=t.col, attrs=[])
        body = self.parse_block()
        return Node('fn', name=name, params=params, ret=ret, body=body, variadic=variadic, line=t.line, col=t.col, attrs=[])

    def parse_struct(self):
        t = self.expect('kw','struct'); name = self.expect('ident').val
        self.expect('op','{'); fields = []
        while not self.at_op('}'):
            fn = self.expect('ident').val; self.expect('op',':'); ft = self.parse_type()
            self.expect('op',';'); fields.append((fn, ft))
        self.expect('op','}')
        return Node('struct', name=name, fields=fields, line=t.line, col=t.col, attrs=[])

    def parse_enum(self):
        t = self.expect('kw','enum'); name = self.expect('ident').val
        self.expect('op','{'); variants = []
        while not self.at_op('}'):
            variants.append(self.expect('ident').val)
            if not self.accept('op',','): break
        self.expect('op','}')
        return Node('enum', name=name, variants=variants, line=t.line)

    def parse_const(self):
        t = self.expect('kw','const'); name = self.expect('ident').val
        self.expect('op',':'); ty = self.parse_type(); self.expect('op','=')
        e = self.parse_expr(); self.expect('op',';')
        return Node('const', name=name, ty=ty, expr=e, line=t.line)

    # ---- tipos
    def parse_type(self):
        t = self.peek()
        if self.accept('op','&'):
            mut = self.accept('kw','mut') is not None
            return T_REF(self.parse_type(), mut)
        if self.accept('op','['):
            if self.accept('op',']'): return T_SLICE(self.parse_type())
            n = self.expect('int'); self.expect('op',']')
            return T_ARRAY(int(n.val, 0), self.parse_type())
        if t.kind == 'ident':
            self.next()
            if t.val in INT_TYPES: return T_INT(t.val)
            if t.val in FLOAT_TYPES: return T_FLOAT(t.val)
            if t.val == 'bool': return T_BOOL
            if t.val == 'void': return T_VOID
            if t.val == 'str': return T_STR
            if t.val == 'ptr':
                self.expect('op','<'); inner = self.parse_type(); self.expect('op','>'); return T_PTR(inner)
            if t.val in self.enum_names: return T_ENUM(t.val)
            return T_STRUCT(t.val)
        self.err(f'tipo inválido {t.val!r}')

    # ---- sentencias
    def parse_block(self):
        t = self.expect('op','{'); stmts = []
        while not self.at_op('}'):
            stmts.append(self.parse_stmt())
        end = self.expect('op','}')
        return Node('block', stmts=stmts, line=t.line, col=t.col, end_line=end.line, end_col=end.col)

    def parse_stmt(self):
        t = self.peek()
        if self.at_kw('var') or self.at_kw('let'):
            mut = self.next().val == 'var'
            name = self.expect('ident').val; ty = None; init = None
            if self.accept('op',':'): ty = self.parse_type()
            if self.accept('op','='): init = self.parse_expr()
            self.expect('op',';')
            if ty is None and init is None: self.err('var sin tipo ni valor inicial', t)
            return Node('var', name=name, ty=ty, init=init, mut=mut, line=t.line, col=t.col)
        if self.at_kw('if'): return self.parse_if()
        if self.at_kw('while'):
            self.next(); self.no_struct_lit += 1; cond = self.parse_expr(); self.no_struct_lit -= 1
            return Node('while', cond=cond, body=self.parse_block(), line=t.line, col=t.col)
        if self.at_kw('loop'):
            self.next(); return Node('loop', body=self.parse_block(), line=t.line, col=t.col)
        if self.at_kw('for'):
            self.next(); var = self.expect('ident').val; self.expect('kw','in')
            self.no_struct_lit += 1
            lo = self.parse_expr(); incl = False
            if self.accept('op','..='): incl = True
            else: self.expect('op','..')
            hi = self.parse_expr(); self.no_struct_lit -= 1
            return Node('for', var=var, lo=lo, hi=hi, incl=incl, body=self.parse_block(), line=t.line, col=t.col)
        if self.at_kw('break'): self.next(); self.expect('op',';'); return Node('break', line=t.line, col=t.col)
        if self.at_kw('continue'): self.next(); self.expect('op',';'); return Node('continue', line=t.line, col=t.col)
        if self.at_kw('return'):
            self.next(); e = None
            if not self.at_op(';'): e = self.parse_expr()
            self.expect('op',';'); return Node('return', expr=e, line=t.line, col=t.col)
        if self.at_kw('defer'):
            self.next(); s = self.parse_stmt(); return Node('defer', stmt=s, line=t.line, col=t.col)
        if self.at_kw('unsafe'):
            self.next(); return Node('unsafe', body=self.parse_block(), line=t.line, col=t.col)
        if self.at_kw('asm'):
            # asm("plantilla" : salidas : entradas : clobbers); se copia tal cual a __asm__ volatile
            self.next(); self.expect('op','(')
            parts, depth = [], 0
            while not (self.at_op(')') and depth == 0):
                tok = self.next()
                if tok.kind == 'eof': self.err('asm sin cerrar', t)
                if tok.kind == 'op' and tok.val == '(': depth += 1
                if tok.kind == 'op' and tok.val == ')': depth -= 1
                parts.append(tok)
            self.expect('op',')'); self.expect('op',';')
            if not parts or parts[0].kind != 'str': self.err('asm requiere una cadena de plantilla', t)
            return Node('asm', parts=parts, line=t.line, col=t.col)
        if self.at_kw('match'): return self.parse_match()
        if self.at_op('{'): return self.parse_block()
        # expresión o asignación
        e = self.parse_expr()
        for op in ('=','+=','-=','*=','/=','%=','&=','|=','^=','<<=','>>='):
            if self.at_op(op):
                self.next(); rhs = self.parse_expr(); self.expect('op',';')
                return Node('assign', op=op, target=e, value=rhs, line=t.line, col=t.col)
        self.expect('op',';')
        return Node('expr', expr=e, line=t.line, col=t.col)

    def parse_if(self):
        t = self.expect('kw','if')
        self.no_struct_lit += 1; cond = self.parse_expr(); self.no_struct_lit -= 1
        then = self.parse_block(); els = None
        if self.accept('kw','else'):
            els = self.parse_if() if self.at_kw('if') else self.parse_block()
        return Node('if', cond=cond, then=then, els=els, line=t.line, col=t.col)

    def parse_match(self):
        t = self.expect('kw','match')
        self.no_struct_lit += 1; subj = self.parse_expr(); self.no_struct_lit -= 1
        self.expect('op','{'); arms = []
        while not self.at_op('}'):
            pats = []
            if self.at('ident','_'):
                self.next(); pats = None
            else:
                # patrones: precedencia por encima de `|` para que `A | B` sean alternativas
                pat_level = self.BIN_LEVELS.index(('|',)) + 1
                pats.append(self.parse_bin(pat_level))
                while self.accept('op','|'): pats.append(self.parse_bin(pat_level))
            self.expect('op','=>')
            body = self.parse_block() if self.at_op('{') else self.parse_stmt()
            self.accept('op',',')
            arms.append((pats, body))
        self.expect('op','}')
        return Node('match', subject=subj, arms=arms, line=t.line, col=t.col)

    # ---- expresiones (precedencia ascendente)
    BIN_LEVELS = [('||',), ('&&',), ('==','!='), ('<','>','<=','>='), ('|',), ('^',), ('&',),
                  ('<<','>>'), ('+','-'), ('*','/','%')]

    def parse_expr(self): return self.parse_bin(0)

    def parse_bin(self, level):
        if level == len(self.BIN_LEVELS): return self.parse_cast()
        left = self.parse_bin(level + 1)
        while self.peek().kind == 'op' and self.peek().val in self.BIN_LEVELS[level]:
            t = self.next(); right = self.parse_bin(level + 1)
            left = Node('bin', op=t.val, l=left, r=right, line=t.line, col=t.col)
        return left

    def parse_cast(self):
        # `as` liga más fuerte que los binarios pero más débil que los unarios:
        # `&x as u64` es `(&x) as u64`, `-x as i32` es `(-x) as i32`.
        e = self.parse_unary()
        while self.at_kw('as'):
            t = self.next(); ty = self.parse_type()
            e = Node('cast', e=e, ty=ty, line=t.line, col=t.col)
        return e

    def parse_unary(self):
        t = self.peek()
        if t.kind == 'op' and t.val in ('-','!','~','*','&'):
            self.next()
            op = t.val
            if op == '&' and self.accept('kw','mut'): op = '&mut'
            e = self.parse_unary()
            return Node('un', op=op, e=e, line=t.line, col=t.col)
        return self.parse_postfix()

    def parse_postfix(self):
        e = self.parse_primary()
        while True:
            t = self.peek()
            if self.at_op('('):
                self.next(); args = []
                while not self.at_op(')'):
                    args.append(self.parse_expr())
                    if not self.accept('op',','): break
                self.expect('op',')')
                e = Node('call', fn=e, args=args, line=t.line, col=t.col)
            elif self.at_op('['):
                self.next()
                lo = hi = None
                if not self.at_op('..'): lo = self.parse_expr()
                if self.accept('op','..'):
                    if not self.at_op(']'): hi = self.parse_expr()
                    self.expect('op',']')
                    e = Node('slice', e=e, lo=lo, hi=hi, line=t.line, col=t.col)
                else:
                    self.expect('op',']')
                    e = Node('index', e=e, i=lo, line=t.line, col=t.col)
            elif self.at_op('.'):
                self.next(); name = self.expect('ident').val
                e = Node('field', e=e, name=name, line=t.line, col=t.col)
            else:
                return e

    def parse_primary(self):
        t = self.next()
        if t.kind == 'int': return Node('int', val=int(t.val, 0), line=t.line, col=t.col)
        if t.kind == 'float': return Node('float', val=t.val, line=t.line, col=t.col)
        if t.kind == 'str':
            val = t.val
            while self.at('str'): val += ' ' + self.next().val   # literales adyacentes se concatenan
            return Node('str', val=val, line=t.line, col=t.col)
        if t.kind == 'char': return Node('char', val=t.val, line=t.line, col=t.col)
        if t.kind == 'kw' and t.val in ('true','false'): return Node('bool', val=t.val, line=t.line, col=t.col)
        if t.kind == 'op' and t.val == '(':
            e = self.parse_expr(); self.expect('op',')'); return e
        if t.kind == 'op' and t.val == '[':
            elems = []
            while not self.at_op(']'):
                elems.append(self.parse_expr())
                if not self.accept('op',','): break
            self.expect('op',']')
            return Node('arraylit', elems=elems, line=t.line, col=t.col)
        if t.kind == 'ident':
            if t.val in self.struct_names and self.at_op('{') and not self.no_struct_lit:
                self.next(); fields = []
                while not self.at_op('}'):
                    fn = self.expect('ident').val; self.expect('op',':'); fields.append((fn, self.parse_expr()))
                    if not self.accept('op',','): break
                self.expect('op','}')
                return Node('structlit', name=t.val, fields=fields, line=t.line, col=t.col)
            return Node('ident', name=t.val, line=t.line, col=t.col)
        raise ErlanError(f'expresión inesperada {t.val!r}', t.line, t.col)

# --------------------------------------------------------------------------- emisor de C
class Emitter:
    def __init__(self, prog, modname):
        self.prog, self.modname = prog, modname
        self.structs = {}      # name -> [(field, type)]
        self.struct_order = []
        self.enums = {}        # name -> [variants]
        self.fns = {}          # name -> (params, ret, variadic, is_extern)
        self.consts = {}       # name -> type
        self.gvars = []        # decls globales (Node gvar)
        self.slices_used = {}  # cname -> elem type
        self.arrays_used = {}  # cname -> (n, elem type): typedefs para &[N]T / ptr<[N]T>
        self.includes = []
        self.out = []
        self.hdr_protos = []
        self.scopes = []       # [{'vars':{name:(type,mut)}, 'defers':[Node], 'loop':bool, 'locals':[(name,type)]}]
        self.unsafe = 0
        self.cur_fn = None
        self.tmp = 0
        # modelo de memoria (ERLAN-MEMORIA.md)
        self.copy_structs = set()   # structs @copy o declarados en extern c (datos planos de C)
        self.drop_fns = {}          # struct -> nombre de la fn @drop
        self.drop_glue = []         # structs/arrays que necesitan función de liberación generada
        self.pending_moves = []     # locales movidos enteros en la sentencia en curso (banderas de drop)
        self.fn_attrs = {}
        self.extern_vars = set()    # datos definidos en C (no se emiten)
        self.imported = {}          # módulo -> ruta (import modulo;)
        self.gasm = []              # ensamblador a nivel de módulo
        self.uses_varargs = False
        self.path = None            # ruta del archivo fuente (para resolver imports)
        self.incdirs = []
        self.modules = None         # cargador compartido con el parser
        self.imported_enums = set() # se declaran en el header del módulo de origen

    def err(self, msg, n): raise ErlanError(msg, getattr(n,'line',0), getattr(n,'col',0))

    # ---- tipos -> C
    def mangle(self, t):
        k = t[0]
        if k in ('int','float'): return t[1]
        if k == 'bool': return 'bool'
        if k == 'str': return 'str'
        if k == 'void': return 'void'
        if k == 'ptr': return 'p' + self.mangle(t[1])
        if k == 'ref': return ('m' if t[2] else 'r') + self.mangle(t[1])
        if k == 'slice': return 's' + self.mangle(t[1])
        if k == 'array': return f'a{t[1]}_' + self.mangle(t[2])
        if k in ('struct','enum'): return 'S' + t[1]
        raise ErlanError(f'tipo no manglable {tstr(t)}')

    def ctype(self, t, n=None):
        k = t[0]
        if k in ('int','float'): return t[1]
        if k == 'bool': return 'bool'
        if k == 'void': return 'void'
        if k == 'str': return 'const char *'
        if k in ('ptr', 'ref'):
            if t[1][0] == 'array':
                # puntero a array: typedef para que el tipo se pueda escribir en casts y temporales
                name = 'erlan_' + self.mangle(t[1]); self.arrays_used[name] = (t[1][1], t[1][2]); self.ctype_walk(t[1][2])
                return name + ' *'
            return self.ctype(t[1]) + ' *'
        if k == 'slice':
            name = 'erlan_' + self.mangle(t); self.slices_used[name] = t[1]; return name
        if k == 'struct':
            if t[1] not in self.structs: self.err(f'struct desconocido {t[1]}', n)
            return 'struct ' + t[1]
        if k == 'enum': return 'enum ' + t[1]
        if k == 'array': self.err('array sólo válido en declaraciones y campos', n)
        if k == 'lit_int': return 'i64'
        if k == 'lit_float': return 'f64'
        self.err(f'tipo no representable {tstr(t)}', n)

    def cdecl(self, t, name, n=None):
        """declarador C para `name` con tipo t (soporta arrays)."""
        if t[0] == 'array': return self.cdecl(t[2], f'{name}[{t[1]}]', n)
        return f'{self.ctype(t, n)} {name}'

    def concrete(self, t):
        if t == T_LITINT: return T_INT('i64')
        if t == T_LITFLOAT: return T_FLOAT('f64')
        return t

    def same(self, a, b): return a == b

    # ---- modelo de memoria: qué se copia, qué se mueve, qué se libera
    def is_copy(self, t):
        """Copy como en Rust: escalares, str, enums, punteros crudos, &T y arrays de Copy.
        Los slices []T son punteros gordos sin dueño en esta rebanada (se copian).
        Un struct sólo es Copy si se declara @copy (o viene de C)."""
        k = t[0]
        if k in ('int','float','lit_int','lit_float','bool','str','void','enum','ptr','fn','enumtype','slice'): return True
        if k == 'ref': return not t[2]
        if k == 'array': return self.is_copy(t[2])
        if k == 'struct': return t[1] in self.copy_structs
        return False

    def needs_drop(self, t, seen=()):
        """Tiene destructor: struct con @drop, struct con algún campo que lo tenga, array de ellos."""
        if t[0] == 'array': return self.needs_drop(t[2], seen)
        if t[0] != 'struct' or t[1] in seen: return False
        if t[1] in self.drop_fns: return True
        return any(self.needs_drop(ft, seen + (t[1],)) for _, ft in self.structs.get(t[1], []))

    def drop_glue_name(self, t):
        name = 'erlan_drop_' + self.mangle(t)
        if t not in self.drop_glue: self.drop_glue.append(t)
        return name

    def emit_drop_glue(self):
        """Funciones de liberación generadas: primero la @drop del usuario, luego los campos en
        orden de declaración (como Rust); los arrays, elemento por elemento."""
        lines, done = [], set()
        i = 0
        while i < len(self.drop_glue):
            t = self.drop_glue[i]; i += 1
            name = 'erlan_drop_' + self.mangle(t)
            if name in done: continue
            done.add(name)
            if t[0] == 'array':
                lines.append(f'static void {name}({self.ctype(t[2])} (*p)[{t[1]}]) {{')
                lines.append(f'    for (u64 i = 0; i < {t[1]}; i++) {self.drop_glue_name(t[2])}(&(*p)[i]);')
                lines.append('}')
            else:
                lines.append(f'static void {name}({self.ctype(t)} *p) {{')
                if t[1] in self.drop_fns: lines.append(f'    {self.drop_fns[t[1]]}(p);')
                for fn, ft in self.structs[t[1]]:
                    if self.needs_drop(ft): lines.append(f'    {self.drop_glue_name(ft)}(&p->{fn});')
                lines.append('}')
        protos = [l.split(' {')[0] + ';' for l in lines if l.startswith('static void')]
        return protos + lines

    VARARGS = {'vararg_start': T_VOID, 'vararg_end': T_VOID, 'vararg_u64': T_INT('u64'), 'vararg_i64': T_INT('i64'),
               'vararg_i32': T_INT('i32'), 'vararg_u32': T_INT('u32'), 'vararg_f64': T_FLOAT('f64'), 'vararg_str': T_STR}

    def import_module(self, name, n, base_path=None, direct=True):
        """Registra las declaraciones públicas de otro módulo Erlan (funciones, structs, enums,
        consts y sus @copy/@drop) a partir de su fuente, y hace que el C incluya su header
        generado. Las variables globales de un módulo son privadas y no se importan. Los
        imports son transitivos: los tipos que usa un módulo importado también se conocen."""
        if name in self.imported or name == self.modname: return
        if self.modules is None: self.modules = Modules(self.incdirs)
        path, prog = self.modules.load(name, base_path or self.path, n)
        self.imported[name] = path
        # Sólo los imports directos incluyen su header: el header de cada módulo ya incluye
        # los de sus propios imports. Los transitivos sólo registran sus declaraciones (tipos).
        if direct and name + '.h' not in self.includes: self.includes.append(name + '.h')
        if prog is None: return
        for d in prog.decls:
            if d.kind == 'import': self.import_module(d.name, d, path, direct=False)
        for d in prog.decls:
            if d.kind == 'struct':
                if d.name in self.structs and self.structs[d.name] != d.fields: self.err(f'import {name}: struct {d.name} ya definido con otro layout', n)
                self.structs[d.name] = d.fields
                if 'copy' in d.attrs: self.copy_structs.add(d.name)
            elif d.kind == 'enum': self.enums[d.name] = d.variants; self.imported_enums.add(d.name)
            elif d.kind == 'fn':
                if d.name in self.fns and d.name not in self.VARARGS and not self.fns[d.name][3]:
                    # el mismo prototipo importado por dos caminos es válido; distinto, no
                    if self.fns[d.name][:3] != (d.params, d.ret, d.variadic): self.err(f'import {name}: la función {d.name} ya existe con otra firma', n)
                self.fns[d.name] = (d.params, d.ret, d.variadic, True)
                self.fn_attrs.setdefault(d.name, d.attrs)
                if 'drop' in d.attrs and d.params and d.params[0][1][0] == 'ref' and d.params[0][1][1][0] == 'struct':
                    self.drop_fns[d.params[0][1][1][1]] = d.name
            elif d.kind == 'extern_c':
                for st in d.structs:
                    if st.name not in self.structs: self.structs[st.name] = st.fields; self.copy_structs.add(st.name)
                for f in d.fns:
                    if f.name not in self.fns: self.fns[f.name] = (f.params, f.ret, f.variadic, True)

    def is_place(self, e):
        if e.kind == 'ident': return self.lookup(e.name) is not None
        if e.kind in ('field','index'): return self.is_place(e.e) or e.e.kind == 'un' and e.e.op == '*'
        if e.kind == 'un' and e.op == '*': return True
        return False

    def place_mutable(self, e, what):
        """Regla de Rust: el deref más cercano al lugar decide (&T: no; &mut T o ptr: sí);
        sin derefs decide la declaración (var sí, let no)."""
        n = e
        while True:
            if n.kind == 'ident':
                v = self.lookup(n.name)
                if v is None: return   # global/const: se comprueba aparte
                if not v[1]: self.err(f'{what}: {n.name} es inmutable (let); declárala con var', e)
                return
            if n.kind in ('field','index'):
                bt = self.expr(n.e)[1]
                if bt[0] == 'ref':
                    if not bt[2]: self.err(f'{what} a través de una referencia compartida &{tstr(bt[1])}; se necesita &mut', e)
                    return
                if bt[0] in ('ptr', 'slice'): return   # vistas crudas (esta rebanada): no mutan la variable
                n = n.e; continue
            if n.kind == 'un' and n.op == '*':
                bt = self.expr(n.e)[1]
                if bt[0] == 'ref' and not bt[2]: self.err(f'{what} a través de una referencia compartida &{tstr(bt[1])}; se necesita &mut', e)
                return
            return

    def coerce(self, code, t, target, n):
        """convierte literal sin tipo al tipo destino; exige igualdad en otro caso."""
        if self.same(t, target): return code
        if t == T_LITINT and (is_int(target) or is_float(target)): return f'(({self.ctype(target)}){code})'
        if t == T_LITFLOAT and is_float(target): return f'(({self.ctype(target)}){code})'
        # Coerciones de referencias como en Rust: &mut T -> &T y &T/&mut T -> ptr<T> (puntero crudo).
        if t[0] == 'ref' and target[0] == 'ref' and self.same(t[1], target[1]) and t[2] and not target[2]: return code
        if t[0] == 'ref' and target[0] == 'ptr' and self.same(t[1], target[1]): return code
        self.err(f'tipos incompatibles: se esperaba {tstr(target)}, se obtuvo {tstr(t)}', n)

    def unify(self, a, b, n):
        if self.same(a, b): return a
        if a == T_LITINT and is_num(b): return b
        if b == T_LITINT and is_num(a): return a
        if a == T_LITFLOAT and b[0] == 'float': return b
        if b == T_LITFLOAT and a[0] == 'float': return a
        self.err(f'tipos incompatibles en operación: {tstr(a)} y {tstr(b)}', n)

    # ---- ámbitos
    def push(self, loop=False): self.scopes.append({'vars':{}, 'defers':[], 'loop':loop, 'locals':[]})
    def pop(self): return self.scopes.pop()
    def declare(self, name, t, mut, n):
        if name in self.scopes[-1]['vars']: self.err(f'variable duplicada {name}', n)
        self.scopes[-1]['vars'][name] = (t, mut)
        self.scopes[-1]['locals'].append((name, t))
    def lookup(self, name):
        for s in reversed(self.scopes):
            if name in s['vars']: return s['vars'][name]
        return None

    # ---- emisión
    def w(self, s, ind=0): self.out.append('    ' * ind + s)

    def run(self):
        # pass 1: registrar
        for d in self.prog.decls:
            if d.kind == 'struct':
                self.structs[d.name] = d.fields; self.struct_order.append(d.name)
                if 'copy' in d.attrs: self.copy_structs.add(d.name)
            elif d.kind == 'enum': self.enums[d.name] = d.variants
            elif d.kind == 'fn':
                self.fns[d.name] = (d.params, d.ret, d.variadic, False)
                self.fn_attrs[d.name] = d.attrs
                if 'drop' in d.attrs:
                    # @drop fn f(x: &mut T): destructor de T; corre al salir del ámbito del valor.
                    if len(d.params) != 1 or d.params[0][1][0] != 'ref' or not d.params[0][1][2] or d.params[0][1][1][0] != 'struct' or d.ret != T_VOID:
                        self.err('@drop requiere exactamente un parámetro &mut Struct y sin valor de retorno', d)
                    sname = d.params[0][1][1][1]
                    if sname in self.drop_fns: self.err(f'{sname} ya tiene destructor ({self.drop_fns[sname]})', d)
                    self.drop_fns[sname] = d.name
            elif d.kind == 'extern_c':
                if d.header: self.includes.append(d.header)
                for f in d.fns: self.fns[f.name] = (f.params, f.ret, f.variadic, d.header is not None)
                for st in d.structs:
                    # se registra el layout para el chequeo de tipos; la definición C viene del header.
                    # Datos planos de C: se copian (como los structs de bindgen en Rust).
                    if st.name in self.structs: self.err(f'struct {st.name} ya definido', st)
                    self.structs[st.name] = st.fields
                    self.copy_structs.add(st.name)
            elif d.kind == 'import_c': self.includes.append(d.header)
            elif d.kind == 'import': self.import_module(d.name, d)
            elif d.kind == 'gasm': self.gasm.append(d)
            elif d.kind == 'const': self.consts[d.name] = d.ty
            elif d.kind == 'gvar':
                if d.ty is None: self.err('las variables globales requieren tipo explícito', d)
                self.gvars.append(d)
        for d in self.prog.decls:
            if d.kind == 'extern_c':
                for v in getattr(d, 'vars', []):
                    self.extern_vars.add(v.name); self.gvars.append(v)
        # comprobaciones del modelo de memoria a nivel de declaración
        for d in self.prog.decls:
            if d.kind == 'struct':
                for fn, ft in d.fields:
                    if ft[0] == 'ref': self.err(f'{d.name}.{fn}: referencias en campos de struct requieren lifetimes (siguiente rebanada)', d)
                    if 'copy' in d.attrs and not self.is_copy(ft): self.err(f'@copy struct {d.name}: el campo {fn} ({tstr(ft)}) no es Copy', d)
                if 'copy' in d.attrs and self.needs_drop(T_STRUCT(d.name)): self.err(f'@copy struct {d.name} no puede tener destructor (Copy y Drop se excluyen)', d)
            if d.kind == 'fn' and d.body is not None:
                refs = [pn for pn, pt in d.params if pt[0] == 'ref']
                if d.ret[0] == 'ref' and len(refs) != 1:
                    self.err(f'{d.name}: devuelve una referencia con {len(refs)} parámetros de referencia; la elisión de Rust exige exactamente uno (lifetimes explícitos: siguiente rebanada)', d)
                if d.ret[0] == 'ref' and 'drop' in d.attrs: self.err('@drop no devuelve nada', d)
            if d.kind == 'gvar' and d.ty[0] == 'ref': self.err('variables globales de tipo referencia: siguiente rebanada', d)
        # ámbito base con las globales
        self.scopes = [{'vars':{}, 'defers':[], 'loop':False, 'locals':[]}]
        for d in self.gvars: self.declare(d.name, d.ty, d.mut, d)
        for d in self.prog.decls:
            if d.kind == 'fn' and d.body is not None and d.variadic:
                self.uses_varargs = True
                for vn, vt in self.VARARGS.items(): self.fns[vn] = ([], vt, False, True)
        # pass 2: cuerpos. Cada función se tipa y se emite a C; después se construye su IR de
        # flujo de control (Mir) a partir del árbol ya tipado y corren el comprobador de
        # movimientos y el de préstamos. Si fallan, el C emitido se descarta con el error.
        body = []
        saved = self.out
        for d in self.prog.decls:
            if d.kind == 'fn' and d.body is not None:
                self.out = []; self.emit_fn(d)
                Mir(self, d).check()
                body.extend(self.out); body.append('')
        self.out = saved
        glue = self.emit_drop_glue()
        # cabecera del archivo
        h = [f'/* generado por erlancompiler v0 a partir de {self.modname}.e — no editar */',
             '#include "erlan_rt.h"']
        if self.uses_varargs: h.append('#include <stdarg.h>')
        h += [f'#include "{inc}"' for inc in self.includes]
        h.append('')
        h += self.type_decls()
        # constantes (privadas al módulo, como las globales)
        for d in self.prog.decls:
            if d.kind == 'const':
                h.append(f'static const {self.cdecl(d.ty, d.name, d)} = {self.init_code(d.expr, d.ty, d)};')
        # ensamblador a nivel de módulo
        for g in self.gasm:
            h.append('__asm__(' + ' '.join(g.parts) + ');')
        # variables globales (privadas al módulo); las de extern c las define el C
        for d in self.gvars:
            if d.name in self.extern_vars: continue
            if d.init is not None:
                code = self.init_code(d.init, d.ty, d)
            else:
                code = '{0}' if d.ty[0] in ('struct','array','slice') else '0'
            attrs = getattr(d, 'attrs', {})
            pre = 'static '
            if 'section' in attrs or 'used' in attrs:
                pre += '__attribute__((' + ', '.join(['used'] + ([f'section("{attrs["section"]}")'] if 'section' in attrs else [])) + ')) '
            if 'volatile' in attrs: pre += 'volatile '
            h.append(f'{pre}{self.cdecl(d.ty, d.name, d)} = {code};')
        # prototipos
        for d in self.prog.decls:
            if d.kind == 'extern_c' and d.header is None:
                for f in d.fns: p = self.proto(f) + ';'; h.append(p); self.hdr_protos.append(p)
        for d in self.prog.decls:
            if d.kind == 'fn' and d.body is not None:
                p = self.proto(d); h.append(p + ';'); self.hdr_protos.append(p + ';')
        h.append('')
        h += glue + ([''] if glue else [])
        return '\n'.join(h + body)

    def type_decls(self):
        lines = []
        for name in self.struct_order: lines.append(f'struct {name};')
        for name, vs in self.enums.items():
            if name in self.imported_enums: continue
            lines.append(f'enum {name} {{ ' + ', '.join(f'{name}_{v}' for v in vs) + ' };')
        # slices: recolectar usos en structs y firmas primero
        for name in self.struct_order:
            for fn, ft in self.structs[name]: self.ctype_walk(ft)
        for fname, (params, ret, _, _) in self.fns.items():
            for _, pt in params: self.ctype_walk(pt)
            self.ctype_walk(ret)
        # los slices usados dentro de cuerpos ya se registraron en pass 2 (run los emite después)
        for sname, et in list(self.slices_used.items()):
            lines.append(f'#ifndef ERLAN_DEF_{sname}\n#define ERLAN_DEF_{sname}\nERLAN_SLICE({sname}, {self.ctype(et)})\n#endif')
        for aname, (n, et) in list(self.arrays_used.items()):
            lines.append(f'#ifndef ERLAN_DEF_{aname}\n#define ERLAN_DEF_{aname}\ntypedef {self.cdecl(et, aname + "[" + str(n) + "]")};\n#endif')
        for name in self.struct_order:
            lines.append(f'struct {name} {{')
            for fn, ft in self.structs[name]: lines.append(f'    {self.cdecl(ft, fn)};')
            lines.append('};')
        lines.append('')
        return lines

    def ctype_walk(self, t):
        if t[0] in ('ptr','slice','ref'): self.ctype_walk(t[1])
        if t[0] == 'array': self.ctype_walk(t[2])
        if t[0] == 'slice' or (t[0] in ('ptr','ref') and t[1][0] == 'array'): self.ctype(t)

    def init_code(self, e, ty, n):
        """Código C de un inicializador con tipo destino conocido: admite literales de array
        (y de struct con arrays) que en C sólo existen como inicializadores `{...}`."""
        if e.kind == 'arraylit':
            if ty[0] != 'array': self.err(f'literal de array para un {tstr(ty)}', e)
            if len(e.elems) != ty[1]: self.err(f'literal de array con {len(e.elems)} elementos para [{ty[1]}]{tstr(ty[2])}', e)
            e.ty = ty
            return '{ ' + ', '.join(self.init_code(x, ty[2], x) for x in e.elems) + ' }'
        if e.kind == 'structlit' and ty[0] == 'struct' and ty[1] == e.name:
            fields = dict(self.structs[e.name]); parts = []
            for fn, fe in e.fields:
                if fn not in fields: self.err(f'{e.name} no tiene campo {fn}', e)
                parts.append(f'.{fn} = {self.init_code(fe, fields[fn], fe)}')
            e.ty = ty
            return '{ ' + ', '.join(parts) + ' }'
        code, t = self.expr(e)
        self.note_move(e, t)
        return self.coerce(code, t, ty, n)

    def proto(self, f):
        ps = [self.cdecl(pt, pn, f) for pn, pt in f.params]
        if f.variadic: ps.append('...')
        if not ps: ps = ['void']
        pre = ''
        attrs = getattr(f, 'attrs', {})
        flags = [a for a in ('noreturn', 'used') if a in attrs] + ([f'section("{attrs["section"]}")'] if 'section' in attrs else [])
        if flags: pre = '__attribute__((' + ', '.join(flags) + ')) '
        return f'{pre}{self.ctype(f.ret, f)} {f.name}(' + ', '.join(ps) + ')'

    def emit_fn(self, f):
        self.cur_fn = f
        self.w(self.proto(f) + ' {')
        if f.variadic: self.w('va_list _erlan_ap;', 1)
        self.push()
        for pn, pt in f.params:
            self.declare(pn, pt, True, f)
            if self.needs_drop(pt): self.w(f'bool {self.live_flag(pn)} = 1;', 1)
        self.emit_block_body(f.body, 1)
        self.pop()
        self.w('}')

    def emit_block_body(self, block, ind):
        for s in block.stmts: self.stmt(s, ind)
        self.emit_scope_exit(self.scopes[-1], ind)

    def emit_defers(self, scope, ind):
        for d in reversed(scope['defers']): self.stmt(d, ind)

    def live_flag(self, name): return f'_erlan_live_{name}'

    def emit_drops(self, scope, ind):
        """Liberación al salir del ámbito: orden inverso de declaración, sólo si el valor sigue
        vivo (no se movió). Las banderas resuelven los movimientos condicionales en ejecución;
        el comprobador de movimientos garantiza que no haya usos después."""
        for name, t in reversed(scope['locals']):
            if self.needs_drop(t):
                self.w(f'if ({self.live_flag(name)}) {{ {self.live_flag(name)} = 0; {self.drop_glue_name(t)}(&{name}); }}', ind)

    def emit_scope_exit(self, scope, ind):
        # primero los defer (pueden usar las locales), luego los destructores
        self.emit_defers(scope, ind)
        self.emit_drops(scope, ind)

    def flush_moves(self, ind):
        """Tras una sentencia que movió locales enteras con destructor: apagar sus banderas."""
        for name in self.pending_moves: self.w(f'{self.live_flag(name)} = 0;', ind)
        self.pending_moves = []

    def note_move(self, e, t):
        """Un valor no-Copy leído desde un lugar se mueve. Se registra la local entera para las
        banderas de drop; mover campos de un struct con destructor o a través de referencias,
        índices o punteros lo rechaza el comprobador de movimientos (Mir)."""
        if self.is_copy(t): return
        base = e
        while base.kind in ('field', 'index'): base = base.e
        if base.kind == 'ident' and self.lookup(base.name) is not None and not self.is_local_name(base.name):
            # las globales son 'static: no se mueve de ellas (Rust: cannot move out of static item)
            self.err(f'no se puede mover `{base.name}`: es una variable global del módulo; usa una referencia, o @copy si es un dato plano', e)
        if e.kind == 'ident' and self.lookup(e.name) is not None and self.is_local_name(e.name) and self.needs_drop(t):
            self.pending_moves.append(e.name)

    def cond_with_moves(self, code, n, ind):
        """Condición que mueve valores: se evalúa en un temporal para apagar banderas antes de ramificar."""
        if not self.pending_moves: return code
        self.tmp += 1; tmp = f'_erlan_c{self.tmp}'
        self.w(f'bool {tmp} = {code};', ind); self.flush_moves(ind)
        return tmp

    def stmt(self, s, ind):
        k = s.kind
        if k == 'var':
            if s.init is not None and s.init.kind == 'arraylit':
                if s.ty is None: self.err('un literal de array necesita el tipo de la variable: var x: [N]T = [...]', s)
                code, t, ty = self.init_code(s.init, s.ty, s), s.ty, s.ty
            elif s.init is not None:
                code, t = self.expr(s.init)
                self.note_move(s.init, t)
                ty = s.ty or self.concrete(t)
                if ty[0] == 'ref' and s.init.kind != 'un' and not self.is_place(s.init) and s.init.kind != 'call':
                    self.err('una referencia sólo nace de &lugar, &mut lugar, otra referencia o una llamada', s)
                code = self.coerce(code, t, ty, s)
                self.w(f'{self.cdecl(ty, s.name, s)} = {code};', ind)
            else:
                ty = s.ty
                if ty[0] == 'ref': self.err('una referencia necesita valor inicial (no hay referencias nulas)', s)
                init = '{0}' if ty[0] in ('struct','array','slice') else '0'
                self.w(f'{self.cdecl(ty, s.name, s)} = {init};', ind)
            s.declty = ty
            self.flush_moves(ind)
            if self.needs_drop(ty): self.w(f'bool {self.live_flag(s.name)} = 1;', ind)
            self.declare(s.name, ty, s.mut, s)
        elif k == 'assign':
            lcode, lt = self.lvalue(s.target)
            rcode, rt = self.expr(s.value)
            if s.op == '=':
                self.note_move(s.value, rt)
                if lt[0] == 'ref' and s.value.kind != 'un' and not self.is_place(s.value) and s.value.kind != 'call':
                    self.err('una referencia sólo nace de &lugar, &mut lugar, otra referencia o una llamada', s)
                rcode = self.coerce(rcode, rt, lt, s)
                if self.needs_drop(lt):
                    # Rust libera el valor anterior al asignar; sólo si seguía vivo.
                    if s.target.kind == 'ident' and self.is_local_name(s.target.name):
                        flag = self.live_flag(s.target.name)
                        self.tmp += 1; tmp = f'_erlan_nv{self.tmp}'
                        self.w(f'{self.cdecl(lt, tmp, s)} = {rcode};', ind)
                        self.w(f'if ({flag}) {self.drop_glue_name(lt)}(&{lcode});', ind)
                        self.w(f'{lcode} = {tmp}; {flag} = 1;', ind)
                    else:
                        self.tmp += 1; tmp = f'_erlan_nv{self.tmp}'
                        self.w(f'{self.cdecl(lt, tmp, s)} = {rcode};', ind)
                        self.w(f'{self.drop_glue_name(lt)}(&{lcode});', ind)
                        self.w(f'{lcode} = {tmp};', ind)
                else:
                    self.w(f'{lcode} = {rcode};', ind)
            else:
                if not is_num(lt): self.err(f'operador {s.op} requiere tipo numérico', s)
                self.w(f'{lcode} {s.op} {self.coerce(rcode, rt, lt, s)};', ind)
            self.flush_moves(ind)
        elif k == 'expr':
            code, t = self.expr(s.expr)
            if self.needs_drop(t):
                # temporal con destructor descartado: se libera al final de la sentencia (Rust)
                self.tmp += 1; tmp = f'_erlan_tmp{self.tmp}'
                self.w(f'{{ {self.cdecl(t, tmp, s)} = {code}; {self.drop_glue_name(t)}(&{tmp}); }}', ind)
            else:
                self.w(code + ';', ind)
            self.flush_moves(ind)
        elif k == 'if':
            c, t = self.expr(s.cond)
            if t != T_BOOL: self.err('la condición de if debe ser bool', s.cond)
            c = self.cond_with_moves(c, s, ind)
            self.w(f'if ({c}) {{', ind); self.block(s.then, ind + 1)
            els = s.els
            while els is not None:
                if els.kind == 'if':
                    c, t = self.expr(els.cond)
                    if t != T_BOOL: self.err('la condición de if debe ser bool', els.cond)
                    if self.pending_moves: self.err('mover un valor en la condición de un else if: evalúalo antes en una variable', els.cond)
                    self.w(f'}} else if ({c}) {{', ind); self.block(els.then, ind + 1); els = els.els
                else:
                    self.w('} else {', ind); self.block(els, ind + 1); els = None
            self.w('}', ind)
        elif k == 'while':
            c, t = self.expr(s.cond)
            if t != T_BOOL: self.err('la condición de while debe ser bool', s.cond)
            if self.pending_moves: self.err('mover un valor en la condición de un while: evalúalo antes en una variable', s.cond)
            self.w(f'while ({c}) {{', ind); self.block(s.body, ind + 1, loop=True); self.w('}', ind)
        elif k == 'loop':
            self.w('for (;;) {', ind); self.block(s.body, ind + 1, loop=True); self.w('}', ind)
        elif k == 'for':
            lo, lt = self.expr(s.lo); hi, ht = self.expr(s.hi)
            it = self.unify(lt, ht, s)
            if it == T_LITINT: it = T_INT('u64')   # rangos literales indexan: u64 como .len
            it = self.concrete(it)
            if not is_int(it): self.err('for requiere rango entero', s)
            cmp = '<=' if s.incl else '<'
            s.ity = it
            self.w(f'for ({self.ctype(it)} {s.var} = {self.coerce(lo, lt, it, s)}; {s.var} {cmp} {self.coerce(hi, ht, it, s)}; {s.var}++) {{', ind)
            self.push(loop=True); self.declare(s.var, it, False, s)
            self.emit_block_body(s.body, ind + 1); self.pop()
            self.w('}', ind)
        elif k == 'break' or k == 'continue':
            # ejecutar defers y destructores de los bloques internos al lazo
            for sc in reversed(self.scopes):
                self.emit_scope_exit(sc, ind)
                if sc['loop']: break
            self.w(k + ';', ind)
        elif k == 'return':
            ret = self.cur_fn.ret
            if s.expr is None:
                if ret != T_VOID: self.err('return sin valor en función con retorno', s)
                for sc in reversed(self.scopes[1:]): self.emit_scope_exit(sc, ind)
                self.w('return;', ind)
            else:
                self.check_escape(s.expr)
                code, t = self.expr(s.expr)
                self.note_move(s.expr, t)
                code = self.coerce(code, t, ret, s)
                has_exit = any(sc['defers'] or any(self.needs_drop(lt) for _, lt in sc['locals']) for sc in self.scopes[1:])
                if has_exit or self.pending_moves:
                    self.tmp += 1; tmp = f'_erlan_ret{self.tmp}'
                    self.w(f'{self.cdecl(ret, tmp, s)} = {code};', ind)
                    self.flush_moves(ind)
                    for sc in reversed(self.scopes[1:]): self.emit_scope_exit(sc, ind)
                    self.w(f'return {tmp};', ind)
                else:
                    self.w(f'return {code};', ind)
        elif k == 'defer':
            self.scopes[-1]['defers'].append(s.stmt)
        elif k == 'unsafe':
            self.unsafe += 1; self.w('{', ind); self.block(s.body, ind + 1); self.w('}', ind); self.unsafe -= 1
        elif k == 'asm':
            if not self.unsafe: self.err('asm sólo dentro de unsafe', s)
            text, prev = [], None
            for tok in s.parts:
                if tok.kind == 'ident' and self.lookup(tok.val) is None and tok.val not in self.consts:
                    self.err(f'asm: operando desconocido {tok.val}', tok)
                sep = '' if (prev is None or tok.val in (',', ':', ')') or prev in ('(',)) else ' '
                text.append(sep + tok.val); prev = tok.val
            self.w('__asm__ volatile (' + ''.join(text) + ');', ind)
        elif k == 'block':
            self.w('{', ind); self.block(s, ind + 1); self.w('}', ind)
        elif k == 'match':
            subj, st = self.expr(s.subject)
            if not self.is_copy(st): self.err('match sólo sobre valores Copy (enteros, enums, bool)', s.subject)
            self.tmp += 1; tmp = f'_erlan_m{self.tmp}'
            self.w(f'{self.cdecl(self.concrete(st), tmp, s)} = {subj};', ind)
            first = True
            for pats, body in s.arms:
                if pats is None: self.w('} else {' if not first else '{', ind)
                else:
                    conds = []
                    for p in pats:
                        pc, pt = self.expr(p); self.unify(st, pt, p); conds.append(f'{tmp} == {pc}')
                    self.w(('} else if (' if not first else 'if (') + ' || '.join(conds) + ') {', ind)
                first = False
                if body.kind == 'block': self.block(body, ind + 1)
                else: self.push(); self.stmt(body, ind + 1); self.pop()
            if not first: self.w('}', ind)
        else:
            self.err(f'sentencia no soportada {k}', s)

    def is_local_name(self, name):
        # local = declarada en un ámbito de función (no en el ámbito base de globales)
        for sc in reversed(self.scopes[1:]):
            if name in sc['vars']: return True
        return False

    def check_escape(self, e):
        """Rechaza devolver referencias a memoria de la pila: &local, &local.campo,
        &local[i], local[..] con array local. Análisis sintáctico; el borrow checker completo es v1."""
        if e.kind == 'un' and e.op in ('&', '&mut'):
            base = e.e
            while base.kind in ('field', 'index'): base = base.e
            if base.kind == 'ident' and self.is_local_name(base.name):
                t = self.lookup(base.name)[0]
                if t[0] not in ('ptr', 'ref') or base is e.e:   # &p.campo con p: ptr<T>/&T apunta a memoria ajena: válido
                    self.err(f'no se puede devolver una referencia a la variable local {base.name}', e)
        if e.kind == 'slice':
            b = e.e
            while b.kind in ('field', 'index'): b = b.e
            if b.kind == 'ident' and self.is_local_name(b.name):
                t = self.lookup(b.name)[0]
                if t[0] == 'array':
                    self.err(f'no se puede devolver una rebanada del array local {b.name}', e)

    def block(self, b, ind, loop=False):
        self.push(loop=loop); self.emit_block_body(b, ind); self.pop()

    # ---- lvalues
    def lvalue(self, e):
        if e.kind == 'ident':
            v = self.lookup(e.name)
            if v is None: self.err(f'variable desconocida {e.name}', e)
            if not v[1]: self.err(f'{e.name} es inmutable (let)', e)
            return e.name, v[0]
        if e.kind == 'field':
            # .len y .ptr de un slice son de sólo lectura: cambiarlos rompería la comprobación de límites
            bt = self.expr(e.e)[1]
            if bt[0] in ('slice', 'array'): self.err(f'.{e.name} de un {tstr(bt)} no es asignable', e)
            self.place_mutable(e, 'asignación')
            return self.expr(e)
        if e.kind == 'index' or (e.kind == 'un' and e.op == '*'):
            self.place_mutable(e, 'asignación')
            return self.expr(e)
        self.err('expresión no asignable', e)

    # ---- expresiones -> (código C, tipo). Cada nodo queda anotado con su tipo (e.ty) para la IR.
    def expr(self, e):
        code, t = self.expr_inner(e)
        e.ty = t
        return code, t

    def expr_inner(self, e):
        k = e.kind
        if k == 'int': return (str(e.val) if e.val < 2**63 else f'{e.val}ULL'), T_LITINT
        if k == 'float': return e.val, T_LITFLOAT
        if k == 'str': return e.val, T_STR
        if k == 'char': return e.val, T_INT('u8')
        if k == 'bool': return e.val, T_BOOL
        if k == 'ident':
            v = self.lookup(e.name)
            if v is not None: return e.name, v[0]
            if e.name in self.consts: return e.name, self.consts[e.name]
            if e.name in self.fns: return e.name, ('fn', e.name)
            if e.name in self.enums: return e.name, ('enumtype', e.name)
            self.err(f'nombre desconocido {e.name}', e)
        if k == 'un':
            c, t = self.expr(e.e)
            if e.op == '-':
                if not is_num(t): self.err('- requiere numérico', e)
                return f'(-{c})', t
            if e.op == '!':
                if t != T_BOOL: self.err('! requiere bool', e)
                return f'(!{c})', T_BOOL
            if e.op == '~':
                if not is_int(t): self.err('~ requiere entero', e)
                return f'(~{c})', t
            if e.op == '&':
                if not self.is_place(e.e): self.err('& requiere un lugar (variable, campo, elemento)', e)
                if t[0] == 'array' and e.e.kind == 'ident': pass
                return f'(&{c})', T_REF(t, False)
            if e.op == '&mut':
                if not self.is_place(e.e): self.err('&mut requiere un lugar (variable, campo, elemento)', e)
                self.place_mutable(e.e, 'préstamo &mut')
                return f'(&{c})', T_REF(t, True)
            if e.op == '*':
                if t[0] not in ('ptr', 'ref'): self.err('* requiere ptr<T>, &T o &mut T', e)
                return f'(*{c})', t[1]
        if k == 'bin':
            lc, lt = self.expr(e.l); rc, rt = self.expr(e.r)
            op = e.op
            if op in ('&&','||'):
                if lt != T_BOOL or rt != T_BOOL: self.err(f'{op} requiere bool', e)
                return f'({lc} {op} {rc})', T_BOOL
            # aritmética de punteros
            if lt[0] == 'ptr' and is_int(rt) and op in ('+','-'):
                if not self.unsafe: self.err('aritmética de punteros requiere unsafe', e)
                return f'({lc} {op} {rc})', lt
            if lt[0] == 'ptr' and rt[0] == 'ptr' and op in ('==','!='):
                return f'({lc} {op} {rc})', T_BOOL
            if (lt[0] == 'ref' or rt[0] == 'ref') and op in ('==','!='):
                self.err('comparar referencias: compara los valores (*a == *b)', e)
            t = self.unify(lt, rt, e)
            # literal op literal: evaluar en 64 bits, no en `int` de C (137 << 40 desbordaría)
            if is_lit(lt) and is_lit(rt):
                w = 'f64' if (is_float(lt) or is_float(rt)) else 'i64'
                lc, rc = f'(({w}){lc})', f'(({w}){rc})'
            if op in ('==','!=','<','>','<=','>='):
                if not (is_num(t) or t[0] in ('enum','bool')): self.err(f'{op} sobre tipo {tstr(t)}', e)
                return f'({self.coerce(lc, lt, t, e)} {op} {self.coerce(rc, rt, t, e)})', T_BOOL
            if op in ('&','|','^','<<','>>','%') and not is_int(t): self.err(f'{op} requiere enteros', e)
            if not is_num(t): self.err(f'{op} sobre tipo {tstr(t)}', e)
            return f'({self.coerce(lc, lt, t, e)} {op} {self.coerce(rc, rt, t, e)})', t
        if k == 'cast':
            c, t = self.expr(e.e); ty = e.ty
            if ty[0] == 'ref': self.err('no se puede crear una referencia con as; usa &lugar', e)
            if t[0] == 'fn':
                # dirección de una función (stubs, trampolines), segura como `f as usize` en Rust
                if ty != T_INT('u64'): self.err('la dirección de una función se toma con `f as u64`', e)
                return f'((u64)(uintptr_t){c})', ty
            if ty[0] == 'ptr' or t[0] in ('ptr', 'ref') or ty == T_STR or t == T_STR:
                if not self.unsafe: self.err('cast de punteros requiere unsafe', e)
            elif not (is_num(t) or t[0] in ('bool','enum')) or not (is_num(ty) or ty[0] in ('bool','enum')):
                self.err(f'cast inválido {tstr(t)} as {tstr(ty)}', e)
            return f'(({self.ctype(ty, e)})({c}))', ty
        if k == 'call':
            if e.fn.kind != 'ident' or e.fn.name not in self.fns:
                self.err('sólo se pueden llamar funciones por nombre', e)
            if e.fn.name in self.VARARGS:
                if not (self.cur_fn and self.cur_fn.variadic): self.err(f'{e.fn.name} sólo dentro de una función variádica (fn f(..., ...))', e)
                if e.args: self.err(f'{e.fn.name} no lleva argumentos', e)
                last = self.cur_fn.params[-1][0]
                vt = self.VARARGS[e.fn.name]
                code = {'vararg_start': f'va_start(_erlan_ap, {last})', 'vararg_end': 'va_end(_erlan_ap)',
                        'vararg_str': 'va_arg(_erlan_ap, const char *)'}.get(e.fn.name, f'va_arg(_erlan_ap, {self.ctype(vt) if vt != T_STR else ""})')
                return code, vt
            params, ret, variadic, _ = self.fns[e.fn.name]
            if len(e.args) < len(params) or (len(e.args) > len(params) and not variadic):
                self.err(f'{e.fn.name}: se esperaban {len(params)} argumentos', e)
            args = []
            for i, a in enumerate(e.args):
                c, t = self.expr(a)
                if i < len(params): c = self.coerce(c, t, params[i][1], a)
                elif is_lit(t): c = f'(({self.ctype(self.concrete(t))}){c})'
                # un argumento &mut se re-presta (reborrow) en la llamada, no se mueve
                if not (t[0] == 'ref' and self.is_place(a)): self.note_move(a, t)
                args.append(c)
            return f'{e.fn.name}({", ".join(args)})', ret
        if k == 'field':
            # Enum.Variante
            if e.e.kind == 'ident' and self.lookup(e.e.name) is None and e.e.name in self.enums:
                if e.name not in self.enums[e.e.name]: self.err(f'variante desconocida {e.e.name}.{e.name}', e)
                return f'{e.e.name}_{e.name}', T_ENUM(e.e.name)
            c, t = self.expr(e.e)
            if t[0] == 'slice':
                if e.name == 'len': return f'{c}.len', T_INT('u64')
                if e.name == 'ptr':
                    if not self.unsafe: self.err('.ptr de un slice requiere unsafe', e)
                    return f'{c}.ptr', T_PTR(t[1])
                self.err(f'slice no tiene campo {e.name}', e)
            if t[0] == 'array':
                if e.name == 'len': return f'((u64){t[1]})', T_INT('u64')
                self.err(f'array no tiene campo {e.name}', e)
            arrow = False
            if t[0] in ('ptr', 'ref') and t[1][0] == 'struct': t = t[1]; arrow = True
            if t[0] == 'ref' and t[1][0] in ('slice', 'array'):
                # &[]T / &[N]T: .len a través de la referencia
                if e.name == 'len': return (f'(*{c}).len' if t[1][0] == 'slice' else f'((u64){t[1][1]})'), T_INT('u64')
            if t[0] != 'struct': self.err(f'acceso a campo sobre {tstr(t)}', e)
            for fn, ft in self.structs[t[1]]:
                if fn == e.name: return f'{c}{"->" if arrow else "."}{e.name}', ft
            self.err(f'struct {t[1]} no tiene campo {e.name}', e)
        if k == 'index':
            c, t = self.expr(e.e); ic, it = self.expr(e.i)
            if not is_int(it): self.err('índice debe ser entero', e)
            ic = self.coerce(ic, it, T_INT('u64'), e) if is_lit(it) else f'((u64)({ic}))'
            if t[0] == 'ref' and t[1][0] in ('slice', 'array'): c = f'(*{c})'; t = t[1]   # auto-deref de &[]T y &[N]T
            if t[0] == 'slice': return f'{c}.ptr[erlan_idx({ic}, {c}.len)]', t[1]
            if t[0] == 'array': return f'{c}[erlan_idx({ic}, {t[1]})]', t[2]
            if t[0] == 'ptr':
                if not self.unsafe: self.err('indexar un ptr requiere unsafe', e)
                return f'{c}[{ic}]', t[1]
            if t == T_STR:
                if not self.unsafe: self.err('indexar un str requiere unsafe (sin longitud conocida)', e)
                return f'((u8){c}[{ic}])', T_INT('u8')
            self.err(f'no se puede indexar {tstr(t)}', e)
        if k == 'slice':
            c, t = self.expr(e.e)
            lo = '0'; hi = None
            if e.lo is not None:
                lc, lt = self.expr(e.lo); lo = self.coerce(lc, lt, T_INT('u64'), e) if is_lit(lt) else f'((u64)({lc}))'
            if e.hi is not None:
                hc, ht = self.expr(e.hi); hi = self.coerce(hc, ht, T_INT('u64'), e) if is_lit(ht) else f'((u64)({hc}))'
            if t[0] == 'ref' and t[1][0] in ('slice', 'array'): c = f'(*{c})'; t = t[1]   # auto-deref
            if t[0] == 'slice':
                base, ln, et = f'{c}.ptr', f'{c}.len', t[1]
            elif t[0] == 'array':
                base, ln, et = c, str(t[1]), t[2]
            elif t[0] == 'ptr':
                if not self.unsafe: self.err('slice desde ptr requiere unsafe', e)
                if hi is None: self.err('slice desde ptr requiere límite superior', e)
                base, ln, et = c, hi, t[1]
            else: self.err(f'no se puede rebanar {tstr(t)}', e)
            if hi is None: hi = ln
            st = T_SLICE(et); sn = self.ctype(st)
            return f'({{ u64 _lo = {lo}, _hi = {hi}; erlan_range(_lo, _hi, {ln}); ({sn}){{ {base} + _lo, _hi - _lo }}; }})', st
        if k == 'structlit':
            if e.name not in self.structs: self.err(f'struct desconocido {e.name}', e)
            fields = dict(self.structs[e.name]); parts = []
            for fn, fe in e.fields:
                if fn not in fields: self.err(f'{e.name} no tiene campo {fn}', e)
                if fe.kind in ('arraylit', 'structlit'): parts.append(f'.{fn} = {self.init_code(fe, fields[fn], fe)}'); continue
                c, t = self.expr(fe); self.note_move(fe, t); parts.append(f'.{fn} = {self.coerce(c, t, fields[fn], fe)}')
            return f'((struct {e.name}){{ {", ".join(parts)} }})', T_STRUCT(e.name)
        if k == 'arraylit':
            self.err('un literal de array sólo vale como inicializador (var x: [N]T = [...], campo de struct o const)', e)
        self.err(f'expresión no soportada {k}', e)

    def header(self):
        g = f'ERLAN_MOD_{self.modname.upper()}_H'
        lines = [f'/* generado por erlancompiler v0 a partir de {self.modname}.e — no editar */',
                 f'#ifndef {g}', f'#define {g}', '#include "erlan_rt.h"']
        lines += [f'#include "{inc}"' for inc in self.includes]
        lines += self.type_decls()
        lines += self.hdr_protos
        lines += [f'#endif /* {g} */', '']
        return '\n'.join(lines)

# --------------------------------------------------------------------------- IR de flujo de control (equivalente al MIR)
class Mir:
    """IR de flujo de control de una función, construida a partir del árbol ya tipado
    (cada expresión lleva su tipo en `.ty`). Es el equivalente del MIR de rustc en lo que
    necesita el modelo de memoria (docs/ERLAN-MEMORIA.md):

      bloques básicos  lista de sentencias y un terminador (goto, switch, return)
      lugares          (local, proyecciones)  proyecciones: ('field', nombre) | ('index',) |
                       ('deref', 'ref'|'ptr')
      operandos        ('copy', lugar) | ('move', lugar) | ('const',)
      sentencias       ('assign', lugar, rvalue)   rvalue: ('use', op) | ('ref', mut, lugar) |
                                                            ('call', fn, [op]) | ('op', [op])
                       ('init', local)  ('drop', local)  ('dead', local)

    Sobre ella corren dos análisis de flujo de datos:
      1. movimientos: conjunto «quizá movido» hacia adelante (uso tras mover, mover dos veces,
         movimiento condicional y en bucles, movimiento parcial de campos);
      2. préstamos con vida por flujo (NLL): un préstamo vive en los puntos donde alguna
         variable que lo contiene está viva (análisis hacia atrás de vida + hacia adelante
         de «qué préstamos contiene cada referencia»); en cada acceso se comprueban los
         conflictos: muchos & o un solo &mut, nada de usar el dueño con un &mut vivo, nada
         de devolver referencias a locales, nada de referencias que sobrevivan a su ámbito.
    La emisión de C sigue recorriendo el árbol; esta IR decide si ese C se acepta."""

    def __init__(self, em, fn):
        self.em, self.fn = em, fn
        self.locals = []     # {'name','ty','mut','kind'}  kind: param | var | temp | ret
        self.blocks = []     # {'stmts': [], 'term': None}
        self.scopes = []     # {'names': {}, 'locals': [], 'defers': [], 'loop': None|(header, exit, cont)}
        self.cur = None
        self.ret = None
        self.loans = []      # {'place','mut','node'}

    # ---- utilidades
    def err(self, msg, n): raise ErlanError(msg, getattr(n, 'line', self.fn.line), getattr(n, 'col', 0))

    def new_block(self):
        self.blocks.append({'stmts': [], 'term': None}); return len(self.blocks) - 1

    def emit(self, st): self.blocks[self.cur]['stmts'].append(st)
    def term(self, t):
        if self.blocks[self.cur]['term'] is None: self.blocks[self.cur]['term'] = t
    def goto(self, b):
        self.term(('goto', b)); self.cur = b

    def new_local(self, name, ty, mut, kind):
        self.locals.append({'name': name, 'ty': ty, 'mut': mut, 'kind': kind}); return len(self.locals) - 1

    def temp(self, ty): return self.new_local(f'_t{len(self.locals)}', ty, True, 'temp')

    def declare(self, name, ty, mut, kind='var'):
        idx = self.new_local(name, ty, mut, kind)
        self.scopes[-1]['names'][name] = idx; self.scopes[-1]['locals'].append(idx)
        return idx

    def lookup(self, name):
        for sc in reversed(self.scopes):
            if name in sc['names']: return sc['names'][name]
        return None

    def push(self, loop=None): self.scopes.append({'names': {}, 'locals': [], 'defers': [], 'loop': loop})

    def is_copy(self, t): return self.em.is_copy(t)
    def is_raw(self, place):
        return any(p[0] == 'deref' and p[1] == 'ptr' for p in place[1])

    def fmt_place(self, place):
        s = self.locals[place[0]]['name']
        for p in place[1]:
            if p[0] == 'field': s += '.' + p[1]
            elif p[0] == 'index': s += '[..]'
            else: s = '*' + s
        return s

    def where(self, n): return f'línea {getattr(n, "line", 0)}:{getattr(n, "col", 0)}'

    # ---- construcción
    def build(self):
        self.push()
        for pn, pt in self.fn.params: self.declare(pn, pt, True, 'param')
        if self.fn.ret != T_VOID: self.ret = self.new_local('_ret', self.fn.ret, True, 'ret')
        self.cur = self.new_block()
        self.lower_block_body(self.fn.body)
        self.exit_scope(self.scopes[-1], self.fn.body)
        self.scopes.pop()
        self.term(('return', self.fn.body))
        # bloques sin terminador (código tras un return): inalcanzables
        for b in self.blocks:
            if b['term'] is None: b['term'] = ('unreachable',)

    def lower_block_body(self, block):
        for s in block.stmts: self.lower_stmt(s)

    def lower_block(self, block, loop=None):
        self.push(loop); self.lower_block_body(block); self.exit_scope(self.scopes[-1], block); self.scopes.pop()

    def exit_scope(self, sc, node):
        """Salida de ámbito: primero los defer (orden inverso), luego los destructores y el fin de
        vida de cada local en orden inverso de declaración (como en Rust)."""
        if hasattr(node, 'end_line'): node = Node('scope_end', line=node.end_line, col=node.end_col)
        for d in reversed(sc['defers']): self.lower_stmt(d)
        for idx in reversed(sc['locals']):
            if self.em.needs_drop(self.locals[idx]['ty']): self.emit(('drop', idx, node))
            self.emit(('dead', idx, node))

    def exit_until_loop(self, node):
        for sc in reversed(self.scopes):
            self.exit_scope(sc, node)
            if sc['loop'] is not None: return sc['loop']
        self.err('break/continue fuera de un bucle', node)

    def lower_stmt(self, s):
        k = s.kind
        if k == 'var':
            ty = s.declty
            if s.init is not None:
                op = self.lower_expr(s.init)
                idx = self.declare(s.name, ty, s.mut)
                self.emit(('assign', (idx, ()), ('use', op), s))
            else:
                idx = self.declare(s.name, ty, s.mut)
                self.emit(('init', idx, s))
        elif k == 'assign':
            if s.op == '=':
                op = self.lower_expr(s.value)
                place = self.lower_place(s.target)
                if place is None: return      # global: fuera del análisis
                self.emit(('assign', place, ('use', op), s))
            else:
                op = self.lower_expr(s.value)
                place = self.lower_place(s.target)
                if place is None: return
                self.emit(('assign', place, ('op', [('copy', place, s), op]), s))
        elif k == 'expr':
            self.lower_expr(s.expr)
        elif k == 'if':
            cond = self.lower_expr(s.cond)
            then_b, join = self.new_block(), self.new_block()
            else_b = self.new_block() if s.els is not None else join
            self.term(('switch', cond, [then_b, else_b], s))
            self.cur = then_b; self.lower_block(s.then); self.goto(join)
            if s.els is not None:
                self.cur = else_b
                if s.els.kind == 'if': self.push(); self.lower_stmt(s.els); self.scopes.pop()
                else: self.lower_block(s.els)
                self.goto(join)
            self.cur = join
        elif k == 'while':
            header, body, exit_b = self.new_block(), self.new_block(), self.new_block()
            self.goto(header)
            cond = self.lower_expr(s.cond)
            self.term(('switch', cond, [body, exit_b], s))
            self.cur = body; self.lower_block(s.body, loop=(header, exit_b, header)); self.goto(header)
            self.cur = exit_b
        elif k == 'loop':
            header, exit_b = self.new_block(), self.new_block()
            self.goto(header)
            self.lower_block(s.body, loop=(header, exit_b, header)); self.goto(header)
            self.cur = exit_b
        elif k == 'for':
            lo = self.lower_expr(s.lo); hi = self.lower_expr(s.hi)
            self.push()
            it = self.declare(s.var, s.ity, False)
            self.scopes[-1]['locals'] = []          # la variable del for muere tras el bucle
            self.emit(('assign', (it, ()), ('use', lo), s))
            header, body, step, exit_b = self.new_block(), self.new_block(), self.new_block(), self.new_block()
            self.goto(header)
            self.term(('switch', ('copy', (it, ()), s), [body, exit_b], s))
            self.cur = body; self.lower_block(s.body, loop=(header, exit_b, step)); self.goto(step)
            self.emit(('assign', (it, ()), ('op', [('copy', (it, ()), s)]), s)); self.goto(header)
            self.cur = exit_b
            self.emit(('dead', it, s))
            self.scopes.pop()
        elif k == 'break' or k == 'continue':
            loop = self.exit_until_loop(s)
            self.term(('goto', loop[1] if k == 'break' else loop[2]))
            self.cur = self.new_block()         # lo que siga es inalcanzable
        elif k == 'return':
            if s.expr is not None:
                op = self.lower_expr(s.expr)
                self.emit(('assign', (self.ret, ()), ('use', op), s))
            for sc in reversed(self.scopes): self.exit_scope(sc, s)
            self.term(('return', s))
            self.cur = self.new_block()
        elif k == 'defer':
            self.scopes[-1]['defers'].append(s.stmt)
        elif k in ('unsafe', 'block'):
            self.lower_block(s.body if k == 'unsafe' else s)
        elif k == 'asm':
            # los operandos nombrados se consideran leídos (el asm es unsafe y no se analiza más)
            for tok in s.parts:
                if tok.kind == 'ident':
                    idx = self.lookup(tok.val)
                    if idx is not None: self.emit(('assign', None, ('op', [('copy', (idx, ()), s)]), s))
        elif k == 'match':
            subj = self.lower_expr(s.subject)
            t = self.temp(getattr(s.subject, 'ty', T_VOID))
            self.emit(('assign', (t, ()), ('use', subj), s))
            arms, join = [], self.new_block()
            for pats, body in s.arms:
                if pats is not None:
                    for p in pats: self.lower_expr(p)
                arms.append(self.new_block())
            self.term(('switch', ('copy', (t, ()), s), arms + [join], s))
            for (pats, body), b in zip(s.arms, arms):
                self.cur = b
                if body.kind == 'block': self.lower_block(body)
                else: self.push(); self.lower_stmt(body); self.exit_scope(self.scopes[-1], body); self.scopes.pop()
                self.goto(join)
            self.cur = join
        else:
            self.err(f'sentencia no soportada en la IR: {k}', s)

    def read_place(self, place, e):
        """Leer un lugar: copia si su tipo es Copy; si no, es un movimiento, y sólo se puede mover
        un lugar propio: ni a través de referencias/punteros, ni un elemento de array, ni un
        campo de un struct con destructor (reglas E0507/E0508/E0509 de Rust)."""
        t = e.ty
        if self.is_copy(t): return ('copy', place, e)
        local, projs = place
        for p in projs:
            if p[0] == 'deref':
                self.err(f'no se puede mover `{self.fmt_place(place)}` a través de una referencia o puntero: el valor ({tstr(t)}) no es Copy; usa una referencia, o @copy si es un dato plano', e)
            if p[0] == 'index':
                self.err(f'no se puede mover un elemento de array `{self.fmt_place(place)}` ({tstr(t)} no es Copy)', e)
        if projs:
            owner = self.locals[local]['ty']
            if self.em.needs_drop(owner):
                self.err(f'no se puede mover el campo `{self.fmt_place(place)}`: {tstr(owner)} tiene destructor (Rust E0509)', e)
        return ('move', place, e)

    def lower_place(self, e):
        """Lugar de una expresión, o None si no es un lugar de esta función (global, constante)."""
        k = e.kind
        if k == 'ident':
            idx = self.lookup(e.name)
            return None if idx is None else (idx, ())
        if k == 'field':
            bt = getattr(e.e, 'ty', None)
            if bt is None: return None                       # Enum.Variante
            base = self.base_place(e.e)
            if bt[0] in ('ref', 'ptr'):
                if bt[1][0] in ('slice', 'array'): return base + ((('deref', bt[0]),),) if False else (base[0], base[1] + (('deref', bt[0]),))
                return (base[0], base[1] + (('deref', bt[0]), ('field', e.name)))
            if bt[0] in ('slice', 'array'): return base         # .len / .ptr: lectura del propio slice
            return (base[0], base[1] + (('field', e.name),))
        if k == 'index':
            bt = e.e.ty
            base = self.base_place(e.e)
            self.lower_expr(e.i)
            if bt[0] in ('ref', 'ptr'): return (base[0], base[1] + (('deref', bt[0]), ('index',)))
            if bt[0] == 'slice' or bt == T_STR: return (base[0], base[1] + (('deref', 'ptr'), ('index',)))
            return (base[0], base[1] + (('index',),))
        if k == 'un' and e.op == '*':
            bt = e.e.ty
            base = self.base_place(e.e)
            return (base[0], base[1] + (('deref', 'ref' if bt[0] == 'ref' else 'ptr'),))
        return None

    def base_place(self, e):
        place = self.lower_place(e)
        if place is not None: return place
        # no es un lugar (llamada, global, literal): se materializa en un temporal
        op = self.lower_expr(e)
        t = self.temp(e.ty)
        self.emit(('assign', (t, ()), ('use', op), e))
        return (t, ())

    def lower_expr(self, e):
        k = e.kind
        if k in ('int', 'float', 'str', 'char', 'bool'): return ('const', None, e)
        if k == 'ident':
            place = self.lower_place(e)
            if place is None: return ('const', None, e)
            return self.read_place(place, e)
        if k == 'un':
            if e.op in ('&', '&mut'):
                place = self.lower_place(e.e)
                t = self.temp(e.ty)
                if place is None: self.emit(('assign', (t, ()), ('op', []), e))   # &global: 'static
                else: self.emit(('assign', (t, ()), ('ref', e.op == '&mut', place), e))
                return (('move' if e.op == '&mut' else 'copy'), (t, ()), e)
            if e.op == '*':
                return self.read_place(self.lower_place(e), e)
            op = self.lower_expr(e.e)
            return self.op_temp([op], e)
        if k == 'bin':
            return self.op_temp([self.lower_expr(e.l), self.lower_expr(e.r)], e)
        if k == 'cast':
            return self.op_temp([self.lower_expr(e.e)], e)
        if k in ('field', 'index'):
            place = self.lower_place(e)
            if place is None: return ('const', None, e)
            return self.read_place(place, e)
        if k == 'slice':
            ops = []
            base = self.lower_place(e.e)
            if base is not None: ops.append(('copy', base, e))
            else: ops.append(self.lower_expr(e.e))
            if e.lo is not None: ops.append(self.lower_expr(e.lo))
            if e.hi is not None: ops.append(self.lower_expr(e.hi))
            return self.op_temp(ops, e)
        if k == 'structlit':
            return self.op_temp([self.lower_expr(fe) for _, fe in e.fields], e)
        if k == 'arraylit':
            return self.op_temp([self.lower_expr(x) for x in e.elems], e)
        if k == 'call':
            params = self.em.fns[e.fn.name][0]
            ops = []
            for a in e.args:
                at = a.ty
                es_lugar = a.kind in ('ident', 'field', 'index') or (a.kind == 'un' and a.op == '*')
                place = self.lower_place(a) if (at[0] == 'ref' and at[2] and es_lugar) else None
                if place is not None:
                    # &mut como argumento: re-préstamo (&mut *p) que vive lo que la llamada o su resultado
                    t = self.temp(at)
                    self.emit(('assign', (t, ()), ('ref', True, (place[0], place[1] + (('deref', 'ref'),))), a))
                    ops.append(('move', (t, ()), a))
                else:
                    ops.append(self.lower_expr(a))
            if e.ty == T_VOID:
                self.emit(('assign', None, ('call', e.fn.name, ops), e))
                return ('const', None, e)
            t = self.temp(e.ty)
            self.emit(('assign', (t, ()), ('call', e.fn.name, ops), e))
            return (('copy' if self.is_copy(e.ty) else 'move'), (t, ()), e)
        self.err(f'expresión no soportada en la IR: {k}', e)

    def op_temp(self, ops, e):
        t = self.temp(e.ty)
        self.emit(('assign', (t, ()), ('op', ops), e))
        return (('copy' if self.is_copy(e.ty) else 'move'), (t, ()), e)

    # ---- accesos de una sentencia, en orden de evaluación: (clase, lugar, nodo)
    #      clases: read, move, borrow, borrow_mut, write (profundo), write_local (superficial), dead
    def accesses(self, st):
        acc = []
        def operand(op):
            if op[0] == 'copy': acc.append(('read', op[1], op[2]))
            elif op[0] == 'move': acc.append(('move', op[1], op[2]))
        k = st[0]
        if k == 'assign':
            place, rv, n = st[1], st[2], st[3]
            if rv[0] == 'use': operand(rv[1])
            elif rv[0] == 'ref': acc.append(('borrow_mut' if rv[1] else 'borrow', rv[2], n))
            elif rv[0] in ('call', 'op'):
                for op in (rv[2] if rv[0] == 'call' else rv[1]): operand(op)
            if place is not None:
                acc.append((('write_local' if not place[1] else 'write'), place, n))
        elif k == 'drop': acc.append(('drop', (st[1], ()), st[2]))   # liberar un valor quizá movido es válido (bandera)
        elif k == 'dead': acc.append(('dead', (st[1], ()), st[2]))
        elif k == 'init': acc.append(('write_local', (st[1], ()), st[2]))
        return acc

    def term_accesses(self, t):
        if t[0] == 'switch' and t[1][0] in ('copy', 'move'): return [('read', t[1][1], t[1][2])]
        if t[0] == 'return' and self.ret is not None: return [('read', (self.ret, ()), t[1])]
        return []

    # ---- análisis 1: movimientos
    def owned_path(self, place):
        """Prefijo propio de un lugar: hasta el primer deref; sin proyecciones de índice."""
        projs = []
        for p in place[1]:
            if p[0] == 'deref': break
            if p[0] == 'field': projs.append(p)
        return (place[0], tuple(projs))

    def is_prefix(self, a, b):
        return a[0] == b[0] and len(a[1]) <= len(b[1]) and b[1][:len(a[1])] == a[1]

    def check_moves(self):
        n = len(self.blocks)
        preds = [[] for _ in range(n)]
        for i, b in enumerate(self.blocks):
            for s in self.successors(b['term']): preds[s].append(i)
        state_in = [dict() for _ in range(n)]
        changed = True
        while changed:
            changed = False
            for i in range(n):
                st = {}
                for p in preds[i]:
                    for k, v in self.move_out(p, state_in[p]).items(): st.setdefault(k, v)
                if i == 0: st = {}
                if st != state_in[i]:
                    state_in[i] = st; changed = True
        for i in range(n): self.move_out(i, state_in[i], report=True)

    def move_out(self, bi, state, report=False):
        st = dict(state)
        b = self.blocks[bi]
        for s in b['stmts']:
            for kind, place, node in self.accesses(s):
                path = self.owned_path(place)
                if kind in ('read', 'move', 'borrow', 'borrow_mut'):
                    if report: self.check_not_moved(path, st, node, kind)
                if kind == 'move':
                    st[path] = node
                elif kind == 'write' and place[1] and place[1][0][0] == 'deref':
                    # escribir a través de una referencia usa la referencia (debe seguir viva)
                    if report: self.check_not_moved(path, st, node, 'read')
                elif kind in ('write', 'write_local'):
                    # asignar a un campo cuyo dueño fue movido es error (Rust E0382); asignar al
                    # propio lugar movido, o a una local entera, lo vuelve a inicializar
                    if kind == 'write' and report:
                        for m, mn in st.items():
                            if self.is_prefix(m, path) and m != path:
                                self.err(f'no se puede asignar a `{self.fmt_place(place)}`: `{self.fmt_place(m)}` fue movido en {self.where(mn)}', node)
                    for m in [m for m in st if self.is_prefix(path, m)]: del st[m]
        for kind, place, node in self.term_accesses(b['term']):
            if report: self.check_not_moved(self.owned_path(place), st, node, kind)
        return st

    def check_not_moved(self, path, st, node, kind):
        for m, mn in st.items():
            if self.is_prefix(m, path):
                hint = '; puede ser en una vuelta anterior del bucle' if getattr(mn, 'line', 0) >= getattr(node, 'line', 0) else ''
                que = 'mover' if kind == 'move' else 'usar'
                quien = 'fue movido' if m == path else f'`{self.fmt_place(m)}` fue movido'
                self.err(f'no se puede {que} `{self.fmt_place(path)}`: {quien} en {self.where(mn)}{hint}', node)
            if self.is_prefix(path, m):
                self.err(f'no se puede {"mover" if kind == "move" else "usar"} `{self.fmt_place(path)}`: su campo `{self.fmt_place(m)}` fue movido en {self.where(mn)}', node)

    def successors(self, t):
        if t[0] == 'goto': return [t[1]]
        if t[0] == 'switch': return list(t[2])
        return []

    # ---- análisis 2: préstamos (NLL)
    def is_ref_local(self, idx): return self.locals[idx]['ty'][0] == 'ref'

    def uses_defs(self, st):
        """(usos, definiciones) de locales en una sentencia, para la vida de las variables."""
        uses, defs = set(), set()
        for kind, place, node in self.accesses(st):
            if kind == 'write_local' or kind == 'dead': defs.add(place[0])
            else: uses.add(place[0])
        return uses, defs

    def check_borrows(self):
        n = len(self.blocks)
        succ = [self.successors(b['term']) for b in self.blocks]
        preds = [[] for _ in range(n)]
        for i in range(n):
            for s in succ[i]: preds[s].append(i)
        # vida hacia atrás, por bloque
        live_in = [set() for _ in range(n)]; live_out = [set() for _ in range(n)]
        changed = True
        while changed:
            changed = False
            for i in reversed(range(n)):
                out = set()
                for s in succ[i]: out |= live_in[s]
                live = set(out)
                for kind, place, node in self.term_accesses(self.blocks[i]['term']): live.add(place[0])
                for st in reversed(self.blocks[i]['stmts']):
                    uses, defs = self.uses_defs(st)
                    live -= defs; live |= uses
                if live != live_in[i] or out != live_out[i]:
                    live_in[i], live_out[i] = live, out; changed = True
        # préstamos que contiene cada referencia, hacia adelante
        held_in = [dict() for _ in range(n)]
        changed = True
        while changed:
            changed = False
            for i in range(n):
                st = {}
                for p in preds[i]:
                    for k, v in self.held_out(p, held_in[p]).items(): st[k] = st.get(k, frozenset()) | v
                if st != held_in[i]:
                    held_in[i] = st; changed = True
        # comprobación de conflictos, sentencia por sentencia
        for i in range(n):
            b = self.blocks[i]
            held = dict(held_in[i])
            # vida por sentencia: se recalcula hacia atrás dentro del bloque
            lives = []
            live = set(live_out[i])
            for kind, place, node in self.term_accesses(b['term']): live.add(place[0])
            term_live = set(live)
            for st in reversed(b['stmts']):
                uses, defs = self.uses_defs(st)
                live_after = set(live)
                live -= defs; live |= uses
                lives.append((set(live), live_after))
            lives.reverse()
            for st, (lv_in, lv_after) in zip(b['stmts'], lives):
                self.check_stmt(st, held, lv_in, lv_after)
                held = self.held_step(st, held)
            t = b['term']
            if t[0] == 'return' and self.ret is not None:
                for L in held.get(self.ret, frozenset()):
                    loan = self.loans[L]
                    root = self.locals[loan['place'][0]]
                    through_deref = loan['place'][1] and loan['place'][1][0][0] == 'deref'
                    if not (root['kind'] == 'param' and through_deref):
                        self.err(f'devuelve una referencia a `{self.fmt_place(loan["place"])}`, que deja de existir al volver (préstamo creado en {self.where(loan["node"])})', t[1])

    def held_out(self, bi, state):
        st = dict(state)
        for s in self.blocks[bi]['stmts']: st = self.held_step(s, st)
        return st

    def kill(self, held, local):
        """Sobrescribir o terminar una referencia `p` mata los préstamos tomados a través de ella
        (`&mut *p`): su camino ya no nombra la misma memoria (regla «kill» de NLL). La memoria sigue
        prestada por los préstamos que el re-préstamo heredó de `p` (ver held_step)."""
        muertos = frozenset(i for i, L in enumerate(self.loans)
                            if L['place'][0] == local and L['place'][1] and L['place'][1][0][0] == 'deref')
        if not muertos: return held
        return {v: s - muertos for v, s in held.items()}

    def held_step(self, st, held):
        held = dict(held)
        k = st[0]
        if k in ('dead', 'init'):
            held = self.kill(held, st[1]); held[st[1]] = frozenset(); return held
        if k != 'assign' or st[1] is None or st[1][1]: return held
        local, rv = st[1][0], st[2]
        held = self.kill(held, local)
        if not self.is_ref_local(local): held[local] = frozenset(); return held
        if rv[0] == 'ref':
            if self.is_raw(rv[2]): held[local] = frozenset()
            else:
                key = (rv[2], rv[1], id(st[3]))
                for i, L in enumerate(self.loans):
                    if L['key'] == key: lid = i; break
                else:
                    self.loans.append({'place': rv[2], 'mut': rv[1], 'node': st[3], 'key': key}); lid = len(self.loans) - 1
                # re-préstamo a través de una referencia: hereda los préstamos de esa referencia
                # (la región del re-préstamo está contenida en la de la referencia original)
                heredados = held.get(rv[2][0], frozenset()) if self.is_ref_local(rv[2][0]) else frozenset()
                held[local] = frozenset([lid]) | heredados
        elif rv[0] == 'use':
            op = rv[1]
            held[local] = held.get(op[1][0], frozenset()) if op[0] in ('copy', 'move') else frozenset()
        elif rv[0] == 'call':
            acc = frozenset()
            for op in rv[2]:
                if op[0] in ('copy', 'move') and self.is_ref_local(op[1][0]): acc |= held.get(op[1][0], frozenset())
            held[local] = acc
        else:
            held[local] = frozenset()
        return held

    def live_loans(self, held, live):
        out = {}
        for v, ls in held.items():
            if v in live or (self.ret is not None and v == self.ret):
                for L in ls: out.setdefault(L, v)
        return out

    def overlaps(self, access, loan_place, shallow):
        a, l = access, loan_place
        if a[0] != l[0]: return False
        if self.is_prefix(l, a): return True
        if self.is_prefix(a, l):
            if not shallow: return True
            rest = l[1][len(a[1]):]
            return not any(p[0] == 'deref' and p[1] == 'ref' for p in rest)
        # mismo prefijo hasta donde difieren: campos distintos son disjuntos
        for pa, pl in zip(a[1], l[1]):
            if pa != pl:
                if pa[0] == 'field' and pl[0] == 'field': return False
                return True
        return True

    def check_stmt(self, st, held, live_in, live_after):
        for kind, place, node in self.accesses(st):
            if self.is_raw(place): continue
            shallow = kind in ('write_local', 'dead')
            # la propia variable que se redefine ya no está viva: sus préstamos mueren con ella
            live = live_in if kind not in ('write_local',) else live_after
            for L, holder in self.live_loans(held, live).items():
                loan = self.loans[L]
                if not self.overlaps(place, loan['place'], shallow): continue
                if kind == 'read' and not loan['mut']: continue
                if kind == 'borrow' and not loan['mut']: continue
                what = self.fmt_place(place); origen = f'préstamo creado en {self.where(loan["node"])}'
                if kind in ('dead', 'drop') and holder == self.ret:
                    self.err(f'devuelve una referencia a la variable local `{what}` ({origen})', node)
                if kind in ('dead', 'drop'):
                    self.err(f'`{what}` no vive lo suficiente: se destruye al salir del ámbito mientras sigue prestado ({origen})', node)
                if kind == 'read':
                    self.err(f'no se puede usar `{what}` mientras hay un préstamo &mut vivo ({origen})', node)
                if kind == 'move':
                    self.err(f'no se puede mover `{what}` mientras está prestado ({origen})', node)
                if kind == 'borrow':
                    self.err(f'no se puede prestar `{what}` como & mientras hay un &mut vivo ({origen})', node)
                if kind == 'borrow_mut':
                    tipo = '&mut' if loan['mut'] else '&'
                    self.err(f'no se puede prestar `{what}` como &mut: ya hay un préstamo {tipo} vivo ({origen})', node)
                self.err(f'no se puede asignar a `{what}` mientras está prestado ({origen})', node)

    # ---- punto de entrada
    def check(self):
        self.build()
        self.check_moves()
        self.check_borrows()
        return self

# --------------------------------------------------------------------------- CLI
def compile_source(src, modname, path=None, incdirs=()):
    modules = Modules(incdirs)
    prog = Parser(lex(src), modules, path).parse_program()
    em = Emitter(prog, modname)
    em.path, em.incdirs, em.modules = path, list(incdirs), modules
    c = em.run()
    return c, em.header()

def main(argv=None):
    ap = argparse.ArgumentParser(prog='erlancompiler', description='compilador Erlan v0')
    ap.add_argument('input'); ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--header', help='emitir también un header C con tipos y prototipos')
    ap.add_argument('-I', dest='incdirs', action='append', default=[], help='directorio donde buscar módulos para import')
    a = ap.parse_args(argv)
    import os
    src = open(a.input, encoding='utf-8').read()
    modname = os.path.splitext(os.path.basename(a.input))[0]
    try:
        c, h = compile_source(src, modname, a.input, a.incdirs)
    except ErlanError as e:
        print(f'{a.input}:{e.line}:{e.col}: error: {e.msg}', file=sys.stderr); return 1
    open(a.output, 'w', encoding='utf-8', newline='\n').write(c)
    if a.header: open(a.header, 'w', encoding='utf-8', newline='\n').write(h)
    return 0

if __name__ == '__main__':
    sys.exit(main())
