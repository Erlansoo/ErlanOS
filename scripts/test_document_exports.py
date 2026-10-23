#!/usr/bin/env python3
"""Verify actual guest snapshots and exported containers, not canned chat text."""
import argparse
from dataclasses import replace
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile
from export_document import decode, frames, layout, checksum, NS

def rejects(fn):
    try: fn()
    except (ValueError,UnicodeError): return
    raise AssertionError('Invalid input accepted')

def main():
    p=argparse.ArgumentParser(); p.add_argument('serial',type=Path); p.add_argument('base',type=Path); args=p.parse_args()
    serial=args.serial.read_text(encoding='utf-8'); snapshots=frames(serial); assert snapshots
    raw=snapshots[-1]; doc=decode(raw)
    assert frames(serial.replace('[system] doc: export begin ELD1','[system] doc: export begin ELD1\n[pit] 123 s'))==snapshots
    # A tailing reader must tolerate arbitrary partial writes until newline.
    for cut in range(max(0,len(serial)-256),len(serial)):
        assert len(frames(serial[:cut]))<=len(snapshots)
    assert frames(serial+'\n[system] doc: export begin ELD1\n[system] 454c')==snapshots
    rejects(lambda: frames(serial.replace('doc: export hash='+str(checksum(raw)),'doc: export hash=0')))
    rejects(lambda: decode(raw[:-1]))
    corrupt=bytearray(raw); struct.pack_into('<Q',corrupt,48,2**64-1); rejects(lambda:decode(corrupt))
    lines,pages=layout(doc)
    assert len(lines)>1
    for line in lines:
        assert line['x']==(doc.right if doc.mirror and line['page']%2==0 else doc.left)
        assert line['x']+sum(line['advances'])<=doc.width-min(doc.left,doc.right)
        assert doc.top<line['y']<=doc.height-doc.bottom
    # Exercise automatic wrapping/pagination independently of explicit page breaks.
    long=replace(doc,text=('Una línea en español con márgenes configurables. '*40+'\n')*25)
    wrapped,n=layout(long); assert n>2
    assert all(len(l['text'].encode())<=224 for l in wrapped)
    from pypdf import PdfReader
    pdf=PdfReader(args.base.with_suffix('.pdf')); assert len(pdf.pages)==pages
    for page in pdf.pages:
        assert abs(float(page.mediabox.width)-doc.width*72/25400)<.02
        assert abs(float(page.mediabox.height)-doc.height*72/25400)<.02
    compact=lambda s:''.join(s.split())
    assert compact(doc.title+doc.text)==compact(''.join(p.extract_text() for p in pdf.pages))
    wordns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    wattr=lambda key:'{'+wordns['w']+'}'+key
    with zipfile.ZipFile(args.base.with_suffix('.docx')) as z:
        root=ET.fromstring(z.read('word/document.xml')); settings=ET.fromstring(z.read('word/settings.xml'))
        styles=ET.fromstring(z.read('word/styles.xml'))
        assert styles.find('.//w:pBdr',wordns) is None
        margins=root.find('.//w:pgMar',wordns); size=root.find('.//w:pgSz',wordns)
        assert abs(int(size.get(wattr('w')))-doc.width*1440/25400)<=1
        for key in ('top','bottom','left','right'): assert abs(int(margins.get(wattr(key)))-getattr(doc,key)*1440/25400)<=1
        assert (settings.find('w:mirrorMargins',wordns) is not None)==bool(doc.mirror)
        content=''.join(el.text or '' for el in root.findall('.//w:t',wordns))
        assert compact(content)==compact(doc.title+doc.text)
        assert len(root.findall('.//w:br[@w:type="page"]',wordns))==doc.text.count('\f')
    with zipfile.ZipFile(args.base.with_suffix('.odt')) as z:
        assert z.infolist()[0].filename=='mimetype' and z.infolist()[0].compress_type==0
        assert z.read('mimetype')==b'application/vnd.oasis.opendocument.text'
        for name in ('styles.xml','content.xml','meta.xml','META-INF/manifest.xml'): ET.fromstring(z.read(name))
        styles=ET.fromstring(z.read('styles.xml'))
        pg=styles.find('.//style:page-layout',NS)
        assert pg.get('{'+NS['style']+'}page-usage')==('mirrored' if doc.mirror else 'all')
        props=pg.find('style:page-layout-properties',NS)
        for key in ('top','bottom','left','right'):
            assert float(props.get('{'+NS['fo']+'}margin-'+key).removesuffix('cm'))==getattr(doc,key)/10000
        content=ET.fromstring(z.read('content.xml'))
        assert compact(''.join(content.itertext()))==compact(doc.title+doc.text)
    print(f'DOCS_EXPORT PASS {pages} pages; exact source text; dimensions; mirror margins; DOCX/ODT structure; corrupt/truncated frame rejection')
if __name__=='__main__': main()
