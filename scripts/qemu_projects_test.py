#!/usr/bin/env python3
"""Real local model: general/project separation, opt-in recognition and reboot."""
import argparse,re,sys
from qemu_workspace_test import WorkspaceSession
from qemu_m4_test import common_arguments,DONE
from workspace_disk import DiskLock
def confirm(vm):
    before=vm.log().count('workspace: contexto restaurado')
    vm.send_key('ret')
    vm.wait_for(lambda s:s.count('workspace: contexto restaurado')>before,'project context restored')
def first(vm):
    vm.ready();vm.command('El tema del chat general es cocina.')
    vm.command('/proyecto nuevo Energia Solar');vm.frame('01-confirmar-proyecto')
    vm.check('no implicit switch','workspace: chat activo=' not in vm.log())
    confirm(vm);vm.command('Mi panel solar entrega doce voltios.')
    vm.command('/general');vm.frame('02-general')
    generated=vm.log().count('chat: generating')
    vm.command('Energia Solar necesita un regulador.')
    vm.check('recognition waits for consent',vm.log().count('chat: generating')==generated)
    vm.frame('03-reconocido');before=vm.log().count(DONE);confirm(vm)
    vm.wait_for(lambda s:s.count(DONE)>before,'pending request answered inside project')
    vm.check('actual model resumed',vm.log().count('chat: generating')>generated)
    vm.frame('04-chat-proyecto')
    project=re.findall(r'workspace: chat activo=(\d+)',vm.log())[-1]
    vm.result['project_id']=int(project)
    vm.check('restored nonempty history',any(int(n)>0 for n in re.findall(r'workspace: historial bytes=(\d+)',vm.log())))
def second(vm):
    vm.ready();vm.command('/proyecto abrir Energia Solar');confirm(vm);vm.frame('05-proyecto-reinicio')
    vm.check('native model restores persisted roles','chat: project history restored' in vm.log())
    counts=re.findall(r'workspace: historial bytes=(\d+)',vm.log());vm.check('saved conversation recovered',counts and int(counts[-1])>100)
    vm.command('/general')
    counts=re.findall(r'workspace: historial bytes=(\d+)',vm.log());vm.check('distinct general transcript',len(counts)>1 and counts[-1]!=counts[-2])
def main():
    p=argparse.ArgumentParser(description=__doc__);common_arguments(p)
    p.set_defaults(accel='whpx',model='models/smollm2-135m-instruct-q8_0.gguf',timeout=300);args=p.parse_args()
    for index,run in enumerate((first,second)):
        vm=WorkspaceSession(args)
        if index==0:disk=vm.directory/'projects.img'
        args.workspace_disk=str(disk)
        with DiskLock(disk):
            failure=None
            try:run(vm)
            except Exception as e:failure=e
            result=vm.finish(failure)
        if result:return result
    return 0
if __name__=='__main__':sys.exit(main())
