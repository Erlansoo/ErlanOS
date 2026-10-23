#!/usr/bin/env python3
"""Visible, private ErlanOS session with automatic host conversion of /doc exportar."""
import argparse
import os
from pathlib import Path
import sys
import time
from qemu_m4_test import QemuSession, common_arguments
from export_document import frames, export_all

def main():
    parser=argparse.ArgumentParser(description=__doc__); common_arguments(parser)
    parser.set_defaults(accel='whpx' if os.name=='nt' else 'kvm',timeout=86400)
    parser.add_argument('--display',default='gtk')
    parser.add_argument('--exports',type=Path,default=Path('output/documentos'))
    parser.add_argument('--smoke',action='store_true',help='Exercise startup, open and export, then close our VM')
    args=parser.parse_args()
    try:
        import reportlab, docx
    except ImportError:
        parser.error('Host export needs reportlab and python-docx. On this PC use scripts/run_document_editor.ps1.')
    vm=QemuSession(args,suite='documents-interactive')
    destination=args.exports.resolve()/vm.directory.name
    completed=0; failure=None
    try:
        vm.start(); print('Exportaciones de esta sesion: '+str(destination),flush=True)
        vm.wait_for(lambda log:'[model] m4: ready' in log,'model ready')
        vm.command('/doc')
        if args.smoke:
            vm.command('/doc titulo Prueba de exportacion'); vm.command('/doc texto Documento creado dentro de ErlanOS.')
            vm.command('/doc exportar')
        while vm.proc.poll() is None:
            snapshots=frames(vm.log())
            for data in snapshots[completed:]:
                name=f'documento-{completed+1:03}'
                export_all(data,destination,name)
                completed+=1
                print(f'[Documentos] Guardado: {destination/name} (.eld .json .pdf .docx .odt)',flush=True)
            if args.smoke:
                if completed!=1: raise RuntimeError('Smoke export missing')
                break
            time.sleep(.5)
        # Drain a final frame if the window closed immediately after exporting.
        for data in frames(vm.log())[completed:]:
            completed+=1; export_all(data,destination,f'documento-{completed:03}')
    except KeyboardInterrupt:
        pass
    except Exception as error:
        failure=error
    vm.result['exports_directory']=str(destination); vm.result['export_count']=completed
    return vm.finish(failure)
if __name__=='__main__': sys.exit(main())
