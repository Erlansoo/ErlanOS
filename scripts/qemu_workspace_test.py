#!/usr/bin/env python3
"""Native D1 acceptance: pointer, empty root, folders, exact save and reboot."""
import argparse, sys, time
from pathlib import Path
from qemu_m4_test import QemuSession,common_arguments,TestFailure
from workspace_disk import DiskLock
from export_document import decode,frames

class WorkspaceSession(QemuSession):
    def __init__(self,args):
        super().__init__(args,suite='workspace'); self.checks=[]; self.xy=None
    def check(self,name,ok):
        self.checks.append({'name':name,'passed':bool(ok)}); self.result['checks']=self.checks
        if not ok: raise TestFailure(name)
    def ready(self):
        self.start();self.wait_for(lambda s:'[model] m4: ready' in s,'ready')
        import re
        self.w,self.h=map(int,re.search(r'framebuffer: (\d+)x(\d+)',self.log()).groups())
        self.xy=(self.w//2,self.h//2)
        self.check('workspace mounted','[storage] volumen Erlan montado' in self.log())
        self.check('PS2 mouse ready','[pointer] PS/2 listo' in self.log())
    def frame(self,name):
        self.execute('screendump',{'filename':name+'.ppm'})
        from PIL import Image
        im=Image.open(self.directory/(name+'.ppm')); im.save(self.directory/(name+'.png'))
    def click(self,x,y,double=False):
        dx,dy=x-self.xy[0],y-self.xy[1]
        self.execute('input-send-event',{'events':[{'type':'rel','data':{'axis':'x','value':dx}},{'type':'rel','data':{'axis':'y','value':dy}}]})
        self.xy=(x,y);time.sleep(.15)
        for _ in range(2 if double else 1):
            for down in (True,False):
                self.execute('input-send-event',{'events':[{'type':'btn','data':{'down':down,'button':'left'}}]});time.sleep(.12)
        time.sleep(.25)

def first(vm):
    vm.ready();vm.frame('01-inicio')
    vm.check('empty workspace','workspace: objetos=0' in vm.log())
    vm.click(vm.w-40,vm.h-35);vm.frame('02-vacio')
    # Native New folder button, name field and Enter.
    x=vm.w-24-min(660,vm.w-48);y=vm.h-92-min(550,vm.h-136)
    vm.click(x+80,y+120);vm.type_text('Documentos');vm.send_key('ret');time.sleep(.5)
    vm.check('folder committed','workspace: commit=1' in vm.log());vm.frame('03-carpeta')
    vm.click(x+160,y+177,True);vm.send_key('esc')
    vm.command('/doc nuevo');vm.command('/doc titulo Informe de energia')
    vm.command('/doc texto Este trabajo conserva su contenido y sus margenes al reiniciar ErlanOS.')
    vm.command('Pon arriba 2 cm, abajo 3, izquierda 2.5 y derecha 4')
    vm.command('/guardar Informe')
    vm.check('document committed','workspace: documento guardado id=2' in vm.log())
    vm.command('/archivos');vm.frame('04-guardado');vm.click(x+180,y+177,True)
    vm.check('double click opened document','workspace: documento abierto id=2' in vm.log())
    vm.command('/doc exportar');vm.frame('05-abierto')
    doc=decode(frames(vm.log())[-1]);vm.result['saved_document']=doc.__dict__
    vm.check('exact margins',(doc.top,doc.bottom,doc.left,doc.right)==(20000,30000,25000,40000))

def second(vm):
    vm.ready();vm.check('two persisted nodes','workspace: objetos=2' in vm.log())
    vm.command('/abrir Documentos');vm.command('/abrir Informe');vm.command('/doc exportar');vm.frame('06-reinicio')
    doc=decode(frames(vm.log())[-1])
    vm.check('persistent content','Este trabajo conserva' in doc.text)
    vm.check('persistent layout',(doc.top,doc.bottom,doc.left,doc.right)==(20000,30000,25000,40000))
    vm.command('/doc texto Una revision posterior.');vm.command('/guardar')
    vm.check('overwrite uses same id','workspace: documento guardado id=2' in vm.log())

def main():
    p=argparse.ArgumentParser(description=__doc__);common_arguments(p)
    p.set_defaults(accel='whpx',model='models/smollm2-135m-instruct-q8_0.gguf',timeout=300)
    args=p.parse_args()
    for index,run in enumerate((first,second)):
        vm=WorkspaceSession(args)
        if index==0: disk=vm.directory/'workspace.img'
        args.workspace_disk=str(disk)
        with DiskLock(disk):
            failure=None
            try:run(vm)
            except Exception as e:failure=e
            code=vm.finish(failure)
        if code:return code
    return 0
if __name__=='__main__':sys.exit(main())
