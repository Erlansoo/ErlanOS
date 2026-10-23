#!/usr/bin/env python3
"""Visible native desktop with persistent documents and project conversations."""
import argparse,os,sys,time
from pathlib import Path
from qemu_m4_test import QemuSession,common_arguments
from workspace_disk import DiskLock
def main():
    p=argparse.ArgumentParser(description=__doc__);common_arguments(p)
    p.set_defaults(accel='whpx' if os.name=='nt' else 'kvm',timeout=86400,model='models/qwen3.5-2b-q8_0.gguf')
    p.add_argument('--workspace-disk',type=Path,default=Path('data/workspace.img'))
    p.add_argument('--display',default='gtk');p.add_argument('--exports',type=Path,default=Path('output/documentos'))
    a=p.parse_args();vm=QemuSession(a,suite='workspace-interactive');failure=None
    # Export is optional; native saving never depends on a host document library.
    try:
        from export_document import frames,export_all
        import reportlab,docx
        convert=True
    except ImportError:convert=False
    completed=0
    with DiskLock(a.workspace_disk.resolve()):
        try:
            vm.start();print('Espacio persistente: '+str(a.workspace_disk.resolve()),flush=True)
            print('Abre Mis archivos con la carpeta de la esquina inferior derecha.',flush=True)
            while vm.proc.poll() is None:
                if convert:
                    for data in frames(vm.log())[completed:]:
                        completed+=1;destination=a.exports.resolve()/vm.directory.name
                        export_all(data,destination,f'documento-{completed:03}')
                        print('Exportado PDF, DOCX y ODT: '+str(destination),flush=True)
                time.sleep(.5)
        except KeyboardInterrupt:pass
        except Exception as e:failure=e
        return vm.finish(failure)
if __name__=='__main__':sys.exit(main())
