#!/usr/bin/env python3
"""Private native document workflow; --draft also runs actual LLM generation."""
import argparse
import sys
from qemu_m4_test import QemuSession, common_arguments, TestFailure
from qemu_ui_test import UiSession
from export_document import decode, frames

class DocumentSession(UiSession):
    def __init__(self,args):
        QemuSession.__init__(self,args,suite='docs')
        self.result['ui_checks']=[]; self.result['frames']=[]

def run(vm):
    vm.wait_for(lambda s:'[model] m4: ready' in s,'model ready')
    vm.geometry()
    def command(text):
        log=vm.command(text)
        vm.check(text+' accepted','[doc]' in log and ' status=0 ' in log and
                 not __import__('re').search(r'\[doc\].* status=[1-9]',log),log)
    command('crea un documento')
    command('/doc titulo Informe de energia solar')
    command('/doc texto Este documento se edita dentro de ErlanOS. Su contenido, margenes y tipografia son propiedades consultables por la IA.')
    command('Pon arriba 2 cm, abajo 3, izquierda 2.5 y derecha 4')
    command('Usa margenes espejo')
    command('Usa fuente serif 12')
    first=vm.snapshot('01-documento')
    vm.check('paper visible',sum(1 for p in first.getdata() if p==(255,255,255))>50000,'01-documento.png')
    command('/doc editar')
    vm.type_text(' ABCX'); vm.send_key('backspace'); vm.send_key('left'); vm.send_key('backspace'); vm.type_text('B'); vm.send_key('end')
    vm.send_key('ret'); vm.type_text('Texto escrito con el teclado.'); vm.send_key('esc')
    command('/doc salto')
    command('/doc texto Esta segunda pagina permite comprobar el intercambio de los margenes interior y exterior para imprimir a doble cara.')
    command('/doc pagina 2')
    second=vm.snapshot('02-espejo')
    paper=[x for x in range(first.width) if first.getpixel((x,88))==(255,255,255)]
    def ink_left(im):
        return min(x for x in range(min(paper),max(paper)+1)
                   if any(max(im.getpixel((x,y)))<120 for y in range(89,im.height-136)))
    displacement=ink_left(second)-ink_left(first)
    expected=len(paper)*15000/210000
    vm.check('mirrored text moved by 15 mm',abs(displacement-expected)<3,f'{displacement} px vs {expected:.2f} px')
    command('/doc exportar')
    vm.check('complete export','doc: export begin ELD1' in vm.log() and 'doc: export hash=' in vm.log() and 'doc: export end ELD1' in vm.log(),vm.serial_path.as_posix())
    document=decode(frames(vm.log())[-1])
    vm.check('actual stored geometry',(document.top,document.bottom,document.left,document.right,document.mirror,document.size10)==(20000,30000,25000,40000,1,120),repr(document))
    vm.check('actual keyboard edits','ABC\nTexto escrito con el teclado.\f' in document.text and 'ABCX' not in document.text,document.text)
    if vm.args.draft:
        command('Redacta un informe breve sobre los beneficios de la energia solar')
        vm.check('actual model generated','[model] chat: generating' in vm.log(),'serial.log')
        vm.snapshot('03-borrador-ia'); command('/doc exportar')
    command('/doc cerrar')
    vm.snapshot('04-cerrado')

def main():
    parser=argparse.ArgumentParser(description=__doc__); common_arguments(parser)
    parser.add_argument('--draft',action='store_true'); args=parser.parse_args()
    vm=DocumentSession(args); failure=None
    try: vm.start(); run(vm)
    except Exception as e: failure=e
    return vm.finish(failure)
if __name__=='__main__': sys.exit(main())
