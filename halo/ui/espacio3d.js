import * as THREE from 'three';
import { OrbitControls } from './vendor/three/OrbitControls.js';

const node = id => document.getElementById(id);
const api = '/api/espacio3d';
const captions = {width:'Ancho', depth:'Profundidad', height:'Altura', radius:'Radio', radius1:'Radio inferior', radius2:'Radio superior',
  length:'Extrusión', distance:'Distancia', segments:'Segmentos', subdivisions:'Subdivisiones', cuts:'Cortes', factor:'Intensidad', iterations:'Pasadas',
  position:'Posición', translation:'Desplazamiento', rotation:'Rotación', scale:'Escala'};

async function request(path = '', body) {
  const response = await fetch(api + path, body ? { method:'POST', headers:{'Content-Type':'application/json', 'X-Halo-Request':'1'}, body:JSON.stringify(body) } : {});
  const value = await response.json();
  if (!response.ok || !value.ok) throw new Error(value.error || `Error ${response.status}`);
  return value;
}

function el(tag, text, cls) {
  const item = document.createElement(tag);
  if (text != null) item.textContent = text;
  if (cls) item.className = cls;
  return item;
}

class Espacio3D {
  constructor() {
    this.doc = null; this.sceneData = null; this.selected = null; this.epoch = 0;
    this.project = new URLSearchParams(location.search).get('project');
    this.hidden = new Set(); this.polling = false; this.job = null; this.statusKey = ''; this.showEdges = true;
    this.stage = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 1000000);
    this.camera.up.set(0, 0, 1); this.camera.position.set(100,-140,110);
    this.renderer = new THREE.WebGLRenderer({ antialias:true, alpha:true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.35;
    this.renderer.localClippingEnabled = true;
    node('e3-canvas').append(this.renderer.domElement);
    this.renderer.domElement.tabIndex = 0;
    this.renderer.domElement.setAttribute('aria-label', 'Lienzo 3D interactivo. Arrastra para orbitar; rueda para zoom.');
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.addEventListener('change', () => this.draw());
    this.stage.add(new THREE.HemisphereLight(0xe9f0ff, 0x6e4930, 2));
    const key = new THREE.DirectionalLight(0xffead3, 3.2); key.position.set(-70,-100,180); this.stage.add(key);
    const rim = new THREE.DirectionalLight(0xa8d5ed, 2.4); rim.position.set(120,70,100); this.stage.add(rim);
    this.group = new THREE.Group(); this.stage.add(this.group);
    this.grid = new THREE.GridHelper(240,24,0x95714a,0x63472f); this.grid.rotation.x=Math.PI/2;
    this.grid.material.transparent=true; this.grid.material.opacity=.24; this.grid.position.z=-.1; this.stage.add(this.grid);
    this.clip = new THREE.Plane(new THREE.Vector3(0,0,-1), 10);
    this.ray = new THREE.Raycaster();
    let start = [0,0];
    this.renderer.domElement.addEventListener('pointerdown', event => { start=[event.clientX,event.clientY]; });
    this.renderer.domElement.addEventListener('pointerup', event => {
      if (event.button !== 0 || Math.hypot(event.clientX-start[0],event.clientY-start[1]) > 5) return;
      const box = this.renderer.domElement.getBoundingClientRect();
      this.ray.setFromCamera(new THREE.Vector2(2*(event.clientX-box.left)/box.width-1,1-2*(event.clientY-box.top)/box.height),this.camera);
      const hit=this.ray.intersectObjects(this.group.children.filter(o=>o.visible),true).find(h=>h.object.isMesh && (!this.section || this.clip.distanceToPoint(h.point)>=0));
      this.select(hit?.object.userData.id || null);
    });
    new ResizeObserver(()=>this.resize()).observe(node('e3-canvas'));
    window.addEventListener('halo-espacio3d-visible', event=>{ if(event.detail) { this.resize(); this.refresh(); } });
    node('e3-fit').onclick=()=>this.fit();
    node('e3-top').onclick=()=>{ const d=Math.max(this.camera.position.distanceTo(this.controls.target),30); this.camera.position.copy(this.controls.target).add(new THREE.Vector3(0,-.001,d)); this.controls.update(); this.draw(); };
    node('e3-wire').onclick=()=>{ this.showEdges=!this.showEdges; for(const obj of this.group.children) for(const child of obj.children) if(child.userData.edge) child.visible=this.showEdges; this.draw(); };
    node('e3-section').onclick=()=>{ this.section=!this.section; this.applySection(); node('e3-section').textContent=this.section?'Quitar sección':'Sección'; };
    node('e3-undo').onclick=()=>this.action({op:'undo'});
    node('e3-redo').onclick=()=>this.action({op:'redo'});
    node('e3-cancel').onclick=()=>this.action({op:'cancel',job:this.job?.id});
    node('e3-new').onclick=()=>{node('e3-new-dialog').showModal();node('e3-new-name').focus();};
    node('e3-new-cancel').onclick=()=>node('e3-new-dialog').close();
    node('e3-new-form').onsubmit=async event=>{event.preventDefault(); const value=await this.action({op:'create',name:node('e3-new-name').value}); if(value) node('e3-new-dialog').close();};
    node('e3-project').onchange=()=>this.chooseProject(node('e3-project').value);
    for(const button of document.querySelectorAll('[data-e3-demo]')) button.onclick=()=>this.demo(button.dataset.e3Demo);
    // Las indicaciones a la IA van por el chat del Halo (panel izquierdo), no por un formulario propio.
    // Sin temporizador: el servidor avisa por SSE cuando cambia un proyecto o un trabajo 3D; sólo mientras un
    // motor está construyendo se consulta cada 1,5 s.
    window.addEventListener('halo-latido',event=>{ if(event.detail.includes('espacio3d') && !node('espacio3d').hidden) this.refresh(); });
    this.resize(); this.refresh();
  }

  message(text,error=false) {node('e3-message').textContent=text;node('e3-message').classList.toggle('error',error);}
  draw() {if(!node('espacio3d').hidden)this.renderer.render(this.stage,this.camera);}
  resize() {const {width,height}=node('e3-canvas').getBoundingClientRect();if(width<1||height<1)return;this.renderer.setSize(width,height,false);this.camera.aspect=width/height;this.camera.updateProjectionMatrix();this.draw();}

  async action(body) {
    try {
      const result=await request('',{project:this.doc?.id,expected_revision:this.doc?.revision,...body});
      if(body.op==='create')this.chooseProject(result.id);
      this.message(result.job?'El motor está construyendo la geometría…':body.op==='note'?'Indicación guardada para el agente.':'Proyecto guardado.');
      await this.refresh(); return result;
    } catch(error) {this.message(error.message,true);return null;}
  }

  chooseProject(id) {
    this.project=id; this.epoch++; this.doc=null;
    const url=new URL(location.href);url.searchParams.set('project',id);url.searchParams.set('pestana','espacio3d');
    history.replaceState(null,'',url);
    window.haloAgents?.setContext(null,null);
    this.refresh();
  }

  async demo(example) {
    const name={cad:'Estudio CAD · soporte',malla:'Estudio de mallas · forma',mixto:'Un espacio · dos motores'}[example];
    try {
      const doc=await request('',{op:'create',name});
      this.chooseProject(doc.id);
      await request('',{op:'demo',project:doc.id,expected_revision:doc.revision,example});
      this.message('Construyendo el ejemplo con los motores reales…');this.refresh();
    } catch(error) {this.message(error.message,true);}
  }

  async refresh() {
    if(this.polling)return;
    this.polling=true;
    const requestEpoch=this.epoch;
    try {
      const overview=await request();
      if(requestEpoch!==this.epoch)return;
      if(!this.project&&overview.active) {
        this.project=overview.active;
        const url=new URL(location.href);url.searchParams.set('project',this.project);history.replaceState(null,'',url);
      }
      const active=this.project;
      const projectSignature=JSON.stringify(overview.projects.map(p=>[p.id,p.name]));
      if(node('e3-project').dataset.signature!==projectSignature) {
        node('e3-project').replaceChildren(...overview.projects.map(p=>new Option(p.name,p.id)));
        if(!overview.projects.length)node('e3-project').append(new Option('Sin proyecto',''));
        node('e3-project').dataset.signature=projectSignature;
      }
      node('e3-project').value=active||'';
      const available=Object.entries(overview.engines).filter(([,x])=>x.available).map(([name])=>name==='freecad'?'CAD':'Mallas');
      node('e3-state').textContent=available.length?`${available.join(' + ')} disponibles`:'Motores no disponibles';
      const activeJob=overview.jobs.find(j=>j.project===active&&['queued','running','cancelling'].includes(j.status));
      this.job=activeJob||null;
      node('e3-progress').hidden=!activeJob;
      clearTimeout(this.seguimiento);
      if(activeJob && !node('espacio3d').hidden) this.seguimiento=setTimeout(()=>this.refresh(),1500);
      node('e3-progress-text').textContent=activeJob?.message||'';
      node('e3-cancel').disabled=activeJob?.status==='cancelling';
      const latest=overview.jobs.find(j=>j.project===active);
      if(latest) {
        const statusKey=latest.id+latest.status;
        if(statusKey!==this.statusKey) {
          this.statusKey=statusKey;
          if(latest.status==='complete')this.message('Guardado · puedes girar, seleccionar y modificar el resultado.');
          else if(['failed','cancelled','interrupted'].includes(latest.status))this.message(latest.error||latest.message,latest.status!=='cancelled');
        }
      }
      if(!active)return;
      const doc=await request('?project='+encodeURIComponent(active));
      if(requestEpoch!==this.epoch)return;
      const changedProject=doc.id!==this.doc?.id;
      const changedRevision=doc.revision!==this.doc?.revision;
      if(changedProject){this.epoch++;this.hidden.clear();this.selected=null;this.section=false;node('e3-section').textContent='Sección';}
      const epoch=this.epoch;
      if(changedProject||changedRevision) {
        const scene=await request('/scene?'+new URLSearchParams({project:doc.id,revision:doc.revision}));
        if(epoch!==this.epoch)return;
        const previousIds=new Set((this.sceneData?.objects||[]).filter(x=>x.visible).map(x=>x.id));
        const newResult=scene.objects.some(x=>x.visible&&!previousIds.has(x.id));
        this.doc=doc;this.sceneData=scene;this.rebuild(scene,changedProject||newResult||!this.group.children.length);
        this.files();this.details();
      } else this.doc=doc;
      window.haloAgents?.setContext(doc,this.selected);
      node('e3-undo').disabled=!!activeJob||!doc.can_undo;
      node('e3-redo').disabled=!!activeJob||!doc.can_redo;
    } catch(error) {this.message(error.message,true);}
    finally {this.polling=false;}
  }

  rebuild(scene,fit) {
    for(const object of [...this.group.children]) {
      object.traverse(child=>{child.geometry?.dispose(); if(child.material)child.material.dispose();});
      this.group.remove(object);
    }
    for(const data of scene.objects) {
      const root=new THREE.Group();root.userData.id=data.id;root.userData.data=data;
      root.visible=data.visible&&!this.hidden.has(data.id);
      const geometry=new THREE.BufferGeometry();
      if(data.positions.length&&data.indices.length) {
        geometry.setAttribute('position',new THREE.Float32BufferAttribute(data.positions,3));geometry.setIndex(data.indices);geometry.computeVertexNormals();
        const material=new THREE.MeshStandardMaterial({color:data.color,roughness:.38,metalness:.2,side:THREE.DoubleSide,flatShading:data.engine==='bmesh'});
        const mesh=new THREE.Mesh(geometry,material);mesh.userData.id=data.id;root.add(mesh);
        const edges=new THREE.LineSegments(new THREE.EdgesGeometry(geometry,24),new THREE.LineBasicMaterial({color:0x281b10,transparent:true,opacity:.5}));
        edges.userData.edge=true;edges.visible=this.showEdges;root.add(edges);
      }
      if(!data.indices.length&&data.lines.length) {
        const lineGeo=new THREE.BufferGeometry();lineGeo.setAttribute('position',new THREE.Float32BufferAttribute(data.lines,3));
        root.add(new THREE.LineSegments(lineGeo,new THREE.LineBasicMaterial({color:data.color})));
      }
      this.group.add(root);
    }
    node('e3-empty').hidden=scene.objects.length>0;
    const visible=scene.objects.filter(o=>o.visible);
    node('e3-count').textContent=`${visible.length} resultado${visible.length===1?'':'s'} · mm`;
    if(!scene.objects.some(o=>o.id===this.selected))this.selected=null;
    this.objects();
    if(fit)this.fit();
    const bounds=this.bounds();
    if(!bounds.isEmpty())this.clip.constant=(bounds.min.z+bounds.max.z)/2;
    this.applySection();this.highlight();this.draw();
  }

  bounds() {const bounds=new THREE.Box3();for(const root of this.group.children)if(root.visible)bounds.union(new THREE.Box3().setFromObject(root));return bounds;}
  fit() {
    const bounds=this.bounds();if(bounds.isEmpty())return;
    const center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());
    const span=Math.max(size.x,size.y,size.z,10);
    this.controls.target.copy(center);
    const distance=span/(2*Math.tan(THREE.MathUtils.degToRad(this.camera.fov/2)))*1.7/Math.min(this.camera.aspect,1);
    this.camera.position.copy(center).add(new THREE.Vector3(.65,-1,.8).normalize().multiplyScalar(distance));
    this.camera.near=Math.max(.01,span/10000);this.camera.far=Math.max(10000,distance*100);this.camera.updateProjectionMatrix();
    this.grid.position.set(center.x,center.y,bounds.min.z-.15);this.grid.scale.setScalar(Math.max(span/120,.3));
    this.controls.update();this.draw();
  }
  applySection() {this.group.traverse(child=>{if(child.material)child.material.clippingPlanes=this.section?[this.clip]:[];});this.draw();}
  select(id) {this.selected=id;this.objects();this.details();this.highlight();window.haloAgents?.setContext(this.doc,id);}
  highlight() {this.group.traverse(child=>{if(child.isMesh)child.material.emissive.setHex(child.userData.id===this.selected?0x3c2107:0);});this.draw();}

  objects() {
    const box=node('e3-objects');box.replaceChildren();
    for(const data of this.sceneData?.objects||[]) {
      if(!data.visible)continue;
      const row=el('div',null,'e3-object');row.classList.toggle('is-selected',data.id===this.selected);
      const select=el('button',data.label);select.title=`${data.label} · ${data.engine==='freecad'?'CAD':'Malla'}`;select.onclick=()=>this.select(data.id);
      const eye=el('button',this.hidden.has(data.id)?'Ver':'Ocultar','e3-eye');eye.setAttribute('aria-label',`${this.hidden.has(data.id)?'Mostrar':'Ocultar'} ${data.label}`);
      eye.onclick=()=>{if(this.hidden.has(data.id))this.hidden.delete(data.id);else this.hidden.add(data.id);const obj=this.group.children.find(o=>o.userData.id===data.id);if(obj)obj.visible=!this.hidden.has(data.id);this.objects();this.draw();};
      row.append(select,eye);box.append(row);
    }
  }

  details(sourceId) {
    const panel=node('e3-details');panel.replaceChildren();
    const data=this.sceneData?.objects.find(o=>o.id===this.selected);
    if(!data){panel.append(el('p','Selecciona una pieza para consultar sus medidas.','e3-muted'));return;}
    panel.append(el('h3',data.label));
    const b=data.bounds,dims=[b[3]-b[0],b[4]-b[1],b[5]-b[2]].map(v=>v.toFixed(2));
    panel.append(el('p',`${data.engine==='freecad'?'CAD · sólido exacto':'Malla'}\n${dims.join(' × ')} mm`));
    if(data.engine==='freecad')panel.append(el('p',`${data.stats.valid?'Geometría válida':'Revisar geometría'} · ${data.stats.solids} sólido(s)`));
    else panel.append(el('p',`${data.stats.vertices} vértices · ${data.stats.closed?'malla cerrada':'malla abierta'}`));
    const ops=this.doc.operations,seen=new Set(),editable=[];
    const visit=id=>{if(seen.has(id))return;seen.add(id);const op=ops.find(x=>x.id===id);if(!op)return;const defaults=op.params||{};if(Object.values(defaults).some(v=>typeof v==='number'||Array.isArray(v)))editable.push(op);for(const parent of op.inputs||[])visit(parent);};
    visit(data.id);
    if(!editable.length)return;
    const source=editable.find(o=>o.id===sourceId)||editable[0];
    const select=el('select');select.setAttribute('aria-label','Elegir dimensiones de una operación');
    for(const op of editable)select.append(new Option(op.label||op.id,op.id));select.value=source.id;
    select.style.width='100%';select.onchange=()=>this.details(select.value);panel.append(select);
    const values=structuredClone(source.params||{});
    for(const [key,value] of Object.entries(values)) {
      if(key==='edges')continue;
      if(typeof value!=='number'&&!Array.isArray(value))continue;
      const parts=Array.isArray(value)?value:[value];
      parts.forEach((v,index)=>{
        const row=el('label',null,'e3-property');row.append(el('span',(captions[key]||key)+(Array.isArray(value)?' '+['X','Y','Z'][index]:'')));
        const input=el('input');input.type='number';input.step=['segments','subdivisions','cuts','iterations'].includes(key)?'1':'0.1';input.value=v;
        input.oninput=()=>{if(Array.isArray(value))values[key][index]=Number(input.value);else values[key]=Number(input.value);};
        row.append(input);panel.append(row);
      });
    }
    const button=el('button','Aplicar dimensiones');
    const project=this.doc.id,revision=this.doc.revision;
    button.onclick=()=>this.action({op:'update',project,expected_revision:revision,id:source.id,params:values});
    panel.append(button);
  }

  files() {
    const box=node('e3-files');box.replaceChildren();
    for(const name of this.doc.artifacts||[]) {
      const link=el('a',name);link.href=api+'/artifact?'+new URLSearchParams({project:this.doc.id,revision:this.doc.revision,name});link.download=name;box.append(link);
    }
    if(!box.children.length)box.append(el('p','Se generan al guardar geometría.','e3-muted'));
  }
}

try {window.haloEspacio3D=new Espacio3D();}
catch(error) {node('e3-message').textContent='No se pudo iniciar el visor 3D: '+error.message;node('e3-message').className='error';}
