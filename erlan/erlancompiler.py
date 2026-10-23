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
"""
import sys, re, argparse

# --------------------------------------------------------------------------- errores
class ErlanError(Exception):
    def __init__(self, msg, line=0, col=0):
        super().__init__(msg); self.msg, self.line, self.col = msg, line, col

# --------------------------------------------------------------------------- lexer
KEYWORDS = {'fn','var','let','const','if','else','while','for','in','loop','break','continue',
            'return','defer','unsafe','extern','struct','enum','match','as','true','false','import'}
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
    if k == 'slice': return f'[]{tstr(t[1])}'
    if k == 'array': return f'[{t[1]}]{tstr(t[2])}'
    if k in ('struct','enum'): return t[1]
    return str(t)

# --------------------------------------------------------------------------- parser
class Parser:
    def __init__(self, toks):
        self.toks, self.i = toks, 0
        self.struct_names = set()   # para desambiguar literales de struct
        self.enum_names = set()
        self.no_struct_lit = 0
        # pre-escaneo de nombres de struct/enum
        for j, t in enumerate(toks[:-1]):
            if t.kind == 'kw' and t.val in ('struct','enum') and toks[j+1].kind == 'ident':
                (self.struct_names if t.val == 'struct' else self.enum_names).add(toks[j+1].val)

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
        return Node('program', decls=decls)

    def parse_decl(self):
        t = self.peek()
        if self.at_kw('fn'): return self.parse_fn()
        if self.at_kw('struct'): return self.parse_struct()
        if self.at_kw('enum'): return self.parse_enum()
        if self.at_kw('const'): return self.parse_const()
        if self.at_kw('var') or self.at_kw('let'):
            st = self.parse_stmt(); st.kind = 'gvar'; return st
        if self.at_kw('extern'): return self.parse_extern()
        if self.at_kw('import'): return self.parse_import()
        self.err(f'declaración inesperada {t.val!r}')

    def parse_import(self):
        t = self.expect('kw','import'); self.expect('ident','c')
        hdr = self.expect('str'); self.expect('op',';')
        return Node('import_c', header=hdr.val[1:-1], line=t.line)

    def parse_extern(self):
        t = self.expect('kw','extern'); self.expect('ident','c')
        header = None
        if self.at('str'): header = self.next().val[1:-1]
        self.expect('op','{')
        fns, structs = [], []
        while not self.at_op('}'):
            if self.at_kw('struct'): structs.append(self.parse_struct())   # struct definido en otro módulo
            else: fns.append(self.parse_fn(proto_only=True))
        self.expect('op','}')
        return Node('extern_c', header=header, fns=fns, structs=structs, line=t.line)

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
            return Node('fn', name=name, params=params, ret=ret, body=None, variadic=variadic, line=t.line)
        body = self.parse_block()
        return Node('fn', name=name, params=params, ret=ret, body=body, variadic=variadic, line=t.line)

    def parse_struct(self):
        t = self.expect('kw','struct'); name = self.expect('ident').val
        self.expect('op','{'); fields = []
        while not self.at_op('}'):
            fn = self.expect('ident').val; self.expect('op',':'); ft = self.parse_type()
            self.expect('op',';'); fields.append((fn, ft))
        self.expect('op','}')
        return Node('struct', name=name, fields=fields, line=t.line)

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
        self.expect('op','}')
        return Node('block', stmts=stmts, line=t.line)

    def parse_stmt(self):
        t = self.peek()
        if self.at_kw('var') or self.at_kw('let'):
            mut = self.next().val == 'var'
            name = self.expect('ident').val; ty = None; init = None
            if self.accept('op',':'): ty = self.parse_type()
            if self.accept('op','='): init = self.parse_expr()
            self.expect('op',';')
            if ty is None and init is None: self.err('var sin tipo ni valor inicial', t)
            return Node('var', name=name, ty=ty, init=init, mut=mut, line=t.line)
        if self.at_kw('if'): return self.parse_if()
        if self.at_kw('while'):
            self.next(); self.no_struct_lit += 1; cond = self.parse_expr(); self.no_struct_lit -= 1
            return Node('while', cond=cond, body=self.parse_block(), line=t.line)
        if self.at_kw('loop'):
            self.next(); return Node('loop', body=self.parse_block(), line=t.line)
        if self.at_kw('for'):
            self.next(); var = self.expect('ident').val; self.expect('kw','in')
            self.no_struct_lit += 1
            lo = self.parse_expr(); incl = False
            if self.accept('op','..='): incl = True
            else: self.expect('op','..')
            hi = self.parse_expr(); self.no_struct_lit -= 1
            return Node('for', var=var, lo=lo, hi=hi, incl=incl, body=self.parse_block(), line=t.line)
        if self.at_kw('break'): self.next(); self.expect('op',';'); return Node('break', line=t.line)
        if self.at_kw('continue'): self.next(); self.expect('op',';'); return Node('continue', line=t.line)
        if self.at_kw('return'):
            self.next(); e = None
            if not self.at_op(';'): e = self.parse_expr()
            self.expect('op',';'); return Node('return', expr=e, line=t.line)
        if self.at_kw('defer'):
            self.next(); s = self.parse_stmt(); return Node('defer', stmt=s, line=t.line)
        if self.at_kw('unsafe'):
            self.next(); return Node('unsafe', body=self.parse_block(), line=t.line)
        if self.at_kw('match'): return self.parse_match()
        if self.at_op('{'): return self.parse_block()
        # expresión o asignación
        e = self.parse_expr()
        for op in ('=','+=','-=','*=','/=','%=','&=','|=','^=','<<=','>>='):
            if self.at_op(op):
                self.next(); rhs = self.parse_expr(); self.expect('op',';')
                return Node('assign', op=op, target=e, value=rhs, line=t.line)
        self.expect('op',';')
        return Node('expr', expr=e, line=t.line)

    def parse_if(self):
        t = self.expect('kw','if')
        self.no_struct_lit += 1; cond = self.parse_expr(); self.no_struct_lit -= 1
        then = self.parse_block(); els = None
        if self.accept('kw','else'):
            els = self.parse_if() if self.at_kw('if') else self.parse_block()
        return Node('if', cond=cond, then=then, els=els, line=t.line)

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
        return Node('match', subject=subj, arms=arms, line=t.line)

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
            self.next(); e = self.parse_unary()
            return Node('un', op=t.val, e=e, line=t.line, col=t.col)
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
        self.includes = []
        self.out = []
        self.hdr_protos = []
        self.scopes = []       # [{'vars':{name:(type,mut)}, 'defers':[Node], 'loop':bool}]
        self.unsafe = 0
        self.cur_fn = None
        self.tmp = 0

    def err(self, msg, n): raise ErlanError(msg, getattr(n,'line',0), getattr(n,'col',0))

    # ---- tipos -> C
    def mangle(self, t):
        k = t[0]
        if k in ('int','float'): return t[1]
        if k == 'bool': return 'bool'
        if k == 'str': return 'str'
        if k == 'ptr': return 'p' + self.mangle(t[1])
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
        if k == 'ptr': return self.ctype(t[1]) + ' *'
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

    def coerce(self, code, t, target, n):
        """convierte literal sin tipo al tipo destino; exige igualdad en otro caso."""
        if self.same(t, target): return code
        if t == T_LITINT and (is_int(target) or is_float(target)): return f'(({self.ctype(target)}){code})'
        if t == T_LITFLOAT and is_float(target): return f'(({self.ctype(target)}){code})'
        self.err(f'tipos incompatibles: se esperaba {tstr(target)}, se obtuvo {tstr(t)}', n)

    def unify(self, a, b, n):
        if self.same(a, b): return a
        if a == T_LITINT and is_num(b): return b
        if b == T_LITINT and is_num(a): return a
        if a == T_LITFLOAT and b[0] == 'float': return b
        if b == T_LITFLOAT and a[0] == 'float': return a
        self.err(f'tipos incompatibles en operación: {tstr(a)} y {tstr(b)}', n)

    # ---- ámbitos
    def push(self, loop=False): self.scopes.append({'vars':{}, 'defers':[], 'loop':loop})
    def pop(self): return self.scopes.pop()
    def declare(self, name, t, mut, n):
        if name in self.scopes[-1]['vars']: self.err(f'variable duplicada {name}', n)
        self.scopes[-1]['vars'][name] = (t, mut)
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
            elif d.kind == 'enum': self.enums[d.name] = d.variants
            elif d.kind == 'fn': self.fns[d.name] = (d.params, d.ret, d.variadic, False)
            elif d.kind == 'extern_c':
                if d.header: self.includes.append(d.header)
                for f in d.fns: self.fns[f.name] = (f.params, f.ret, f.variadic, d.header is not None)
                for st in d.structs:
                    # se registra el layout para el chequeo de tipos; la definición C viene del header
                    if st.name in self.structs: self.err(f'struct {st.name} ya definido', st)
                    self.structs[st.name] = st.fields
            elif d.kind == 'import_c': self.includes.append(d.header)
            elif d.kind == 'const': self.consts[d.name] = d.ty
            elif d.kind == 'gvar':
                if d.ty is None: self.err('las variables globales requieren tipo explícito', d)
                self.gvars.append(d)
        # ámbito base con las globales
        self.scopes = [{'vars':{}, 'defers':[], 'loop':False}]
        for d in self.gvars: self.declare(d.name, d.ty, d.mut, d)
        # pass 2: cuerpos
        body = []
        saved = self.out
        for d in self.prog.decls:
            if d.kind == 'fn' and d.body is not None:
                self.out = []; self.emit_fn(d); body.extend(self.out); body.append('')
        self.out = saved
        # cabecera del archivo
        h = [f'/* generado por erlancompiler v0 a partir de {self.modname}.e — no editar */',
             '#include "erlan_rt.h"']
        h += [f'#include "{inc}"' for inc in self.includes]
        h.append('')
        h += self.type_decls()
        # constantes
        for d in self.prog.decls:
            if d.kind == 'const':
                code, t = self.expr(d.expr)
                h.append(f'static const {self.cdecl(d.ty, d.name, d)} = {self.coerce(code, t, d.ty, d)};')
        # variables globales (privadas al módulo)
        for d in self.gvars:
            if d.init is not None:
                code, t = self.expr(d.init); code = self.coerce(code, t, d.ty, d)
            else:
                code = '{0}' if d.ty[0] in ('struct','array','slice') else '0'
            h.append(f'static {self.cdecl(d.ty, d.name, d)} = {code};')
        # prototipos
        for d in self.prog.decls:
            if d.kind == 'extern_c' and d.header is None:
                for f in d.fns: h.append(self.proto(f) + ';')
        for d in self.prog.decls:
            if d.kind == 'fn' and d.body is not None:
                p = self.proto(d); h.append(p + ';'); self.hdr_protos.append(p + ';')
        h.append('')
        return '\n'.join(h + body)

    def type_decls(self):
        lines = []
        for name in self.struct_order: lines.append(f'struct {name};')
        for name, vs in self.enums.items():
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
        for name in self.struct_order:
            lines.append(f'struct {name} {{')
            for fn, ft in self.structs[name]: lines.append(f'    {self.cdecl(ft, fn)};')
            lines.append('};')
        lines.append('')
        return lines

    def ctype_walk(self, t):
        if t[0] in ('ptr','slice'): self.ctype_walk(t[1])
        if t[0] == 'array': self.ctype_walk(t[2])
        if t[0] == 'slice': self.ctype(t)

    def proto(self, f):
        ps = [self.cdecl(pt, pn, f) for pn, pt in f.params]
        if f.variadic: ps.append('...')
        if not ps: ps = ['void']
        return f'{self.ctype(f.ret, f)} {f.name}(' + ', '.join(ps) + ')'

    def emit_fn(self, f):
        self.cur_fn = f
        self.w(self.proto(f) + ' {')
        self.push()
        for pn, pt in f.params: self.declare(pn, pt, True, f)
        self.emit_block_body(f.body, 1)
        self.pop()
        self.w('}')

    def emit_block_body(self, block, ind):
        for s in block.stmts: self.stmt(s, ind)
        self.emit_defers(self.scopes[-1], ind)

    def emit_defers(self, scope, ind):
        for d in reversed(scope['defers']): self.stmt(d, ind)

    def stmt(self, s, ind):
        k = s.kind
        if k == 'var':
            if s.init is not None:
                code, t = self.expr(s.init)
                ty = s.ty or self.concrete(t)
                code = self.coerce(code, t, ty, s)
                self.w(f'{self.cdecl(ty, s.name, s)} = {code};', ind)
            else:
                ty = s.ty
                init = '{0}' if ty[0] in ('struct','array','slice') else '0'
                self.w(f'{self.cdecl(ty, s.name, s)} = {init};', ind)
            self.declare(s.name, ty, s.mut, s)
        elif k == 'assign':
            lcode, lt = self.lvalue(s.target)
            rcode, rt = self.expr(s.value)
            if s.op == '=':
                self.w(f'{lcode} = {self.coerce(rcode, rt, lt, s)};', ind)
            else:
                if not is_num(lt): self.err(f'operador {s.op} requiere tipo numérico', s)
                self.w(f'{lcode} {s.op} {self.coerce(rcode, rt, lt, s)};', ind)
        elif k == 'expr':
            code, t = self.expr(s.expr); self.w(code + ';', ind)
        elif k == 'if':
            c, t = self.expr(s.cond)
            if t != T_BOOL: self.err('la condición de if debe ser bool', s.cond)
            self.w(f'if ({c}) {{', ind); self.block(s.then, ind + 1)
            els = s.els
            while els is not None:
                if els.kind == 'if':
                    c, t = self.expr(els.cond)
                    if t != T_BOOL: self.err('la condición de if debe ser bool', els.cond)
                    self.w(f'}} else if ({c}) {{', ind); self.block(els.then, ind + 1); els = els.els
                else:
                    self.w('} else {', ind); self.block(els, ind + 1); els = None
            self.w('}', ind)
        elif k == 'while':
            c, t = self.expr(s.cond)
            if t != T_BOOL: self.err('la condición de while debe ser bool', s.cond)
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
            self.w(f'for ({self.ctype(it)} {s.var} = {self.coerce(lo, lt, it, s)}; {s.var} {cmp} {self.coerce(hi, ht, it, s)}; {s.var}++) {{', ind)
            self.push(loop=True); self.declare(s.var, it, False, s)
            self.emit_block_body(s.body, ind + 1); self.pop()
            self.w('}', ind)
        elif k == 'break' or k == 'continue':
            # ejecutar defers de los bloques internos al lazo
            for sc in reversed(self.scopes):
                self.emit_defers(sc, ind)
                if sc['loop']: break
            self.w(k + ';', ind)
        elif k == 'return':
            ret = self.cur_fn.ret
            if s.expr is None:
                if ret != T_VOID: self.err('return sin valor en función con retorno', s)
                for sc in reversed(self.scopes): self.emit_defers(sc, ind)
                self.w('return;', ind)
            else:
                self.check_escape(s.expr)
                code, t = self.expr(s.expr)
                code = self.coerce(code, t, ret, s)
                has_defers = any(sc['defers'] for sc in self.scopes)
                if has_defers:
                    self.tmp += 1; tmp = f'_erlan_ret{self.tmp}'
                    self.w(f'{self.cdecl(ret, tmp, s)} = {code};', ind)
                    for sc in reversed(self.scopes): self.emit_defers(sc, ind)
                    self.w(f'return {tmp};', ind)
                else:
                    self.w(f'return {code};', ind)
        elif k == 'defer':
            self.scopes[-1]['defers'].append(s.stmt)
        elif k == 'unsafe':
            self.unsafe += 1; self.w('{', ind); self.block(s.body, ind + 1); self.w('}', ind); self.unsafe -= 1
        elif k == 'block':
            self.w('{', ind); self.block(s, ind + 1); self.w('}', ind)
        elif k == 'match':
            subj, st = self.expr(s.subject)
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
        if e.kind == 'un' and e.op == '&':
            base = e.e
            while base.kind in ('field', 'index'): base = base.e
            if base.kind == 'ident' and self.is_local_name(base.name):
                t = self.lookup(base.name)[0]
                if t[0] != 'ptr':   # &p.campo con p: ptr<T> apunta a memoria ajena: válido
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
            return self.expr(e)
        if e.kind == 'index' or (e.kind == 'un' and e.op == '*'):
            return self.expr(e)
        self.err('expresión no asignable', e)

    # ---- expresiones -> (código C, tipo)
    def expr(self, e):
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
                return f'(&{c})', T_PTR(t)
            if e.op == '*':
                if t[0] != 'ptr': self.err('* requiere ptr<T>', e)
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
            if ty[0] == 'ptr' or t[0] == 'ptr' or ty == T_STR or t == T_STR:
                if not self.unsafe: self.err('cast de punteros requiere unsafe', e)
            elif not (is_num(t) or t[0] in ('bool','enum')) or not (is_num(ty) or ty[0] in ('bool','enum')):
                self.err(f'cast inválido {tstr(t)} as {tstr(ty)}', e)
            return f'(({self.ctype(ty, e)})({c}))', ty
        if k == 'call':
            if e.fn.kind != 'ident' or e.fn.name not in self.fns:
                self.err('sólo se pueden llamar funciones por nombre', e)
            params, ret, variadic, _ = self.fns[e.fn.name]
            if len(e.args) < len(params) or (len(e.args) > len(params) and not variadic):
                self.err(f'{e.fn.name}: se esperaban {len(params)} argumentos', e)
            args = []
            for i, a in enumerate(e.args):
                c, t = self.expr(a)
                if i < len(params): c = self.coerce(c, t, params[i][1], a)
                elif is_lit(t): c = f'(({self.ctype(self.concrete(t))}){c})'
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
            if t[0] == 'ptr' and t[1][0] == 'struct': t = t[1]; arrow = True
            if t[0] != 'struct': self.err(f'acceso a campo sobre {tstr(t)}', e)
            for fn, ft in self.structs[t[1]]:
                if fn == e.name: return f'{c}{"->" if arrow else "."}{e.name}', ft
            self.err(f'struct {t[1]} no tiene campo {e.name}', e)
        if k == 'index':
            c, t = self.expr(e.e); ic, it = self.expr(e.i)
            if not is_int(it): self.err('índice debe ser entero', e)
            ic = self.coerce(ic, it, T_INT('u64'), e) if is_lit(it) else f'((u64)({ic}))'
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
                c, t = self.expr(fe); parts.append(f'.{fn} = {self.coerce(c, t, fields[fn], fe)}')
            return f'((struct {e.name}){{ {", ".join(parts)} }})', T_STRUCT(e.name)
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

# --------------------------------------------------------------------------- CLI
def compile_source(src, modname):
    prog = Parser(lex(src)).parse_program()
    em = Emitter(prog, modname)
    c = em.run()
    return c, em.header()

def main(argv=None):
    ap = argparse.ArgumentParser(prog='erlancompiler', description='compilador Erlan v0')
    ap.add_argument('input'); ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--header', help='emitir también un header C con tipos y prototipos')
    a = ap.parse_args(argv)
    import os
    src = open(a.input, encoding='utf-8').read()
    modname = os.path.splitext(os.path.basename(a.input))[0]
    try:
        c, h = compile_source(src, modname)
    except ErlanError as e:
        print(f'{a.input}:{e.line}:{e.col}: error: {e.msg}', file=sys.stderr); return 1
    open(a.output, 'w', encoding='utf-8', newline='\n').write(c)
    if a.header: open(a.header, 'w', encoding='utf-8', newline='\n').write(h)
    return 0

if __name__ == '__main__':
    sys.exit(main())
