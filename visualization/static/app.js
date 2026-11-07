'use strict';
const $ = id => document.getElementById(id);
const S = {sources:[], datasets:[], models:[], runs:[], dataset:null, sensor:null, model:null, run:null, view:'overview', polling:false, plotToken:0};
const nf = new Intl.NumberFormat('ru-RU');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const metric = value => value === null || value === undefined ? '—' : value.toFixed(3);
const date = value => value ? new Date(value).toLocaleDateString('ru-RU',{timeZone:'UTC'}) : '—';
const sensorName = id => Object.assign({}, ...S.sources.map(x=>x.sensors || {}))[id] || id;
const currentDataset = () => S.datasets.find(d=>d.id === S.dataset);
const currentModel = () => S.models.find(m=>m.id === S.model);
const statusText = x => ({created:'Создана',queued:'В очереди',training:'Обучается',ready:'Готова',failed:'Ошибка',interrupted:'Прервано'}[x] || x);

async function api(path, options={}) {
  const request = {...options};
  if (request.body && !(request.body instanceof FormData)) {
    request.headers = {'Content-Type':'application/json', ...request.headers};
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch('/api'+path,request);
  const payload = await response.json().catch(()=>({detail:'Не удалось прочитать ответ сервиса'}));
  if (!response.ok) {
    let detail = payload.detail;
    if (Array.isArray(detail)) detail = detail.map(x=>x.msg).join('; ');
    throw new Error(typeof detail === 'string' ? detail : 'Операция не выполнена');
  }
  return payload;
}
let toastTimer;
function toast(message,error=false) {
  $('toast').textContent=message; $('toast').className='toast'+(error?' error':''); $('toast').hidden=false;
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,error?9000:4500);
}
function showError(error) {
  $('error-banner').textContent=error.message || String(error); $('error-banner').hidden=false;
  toast(error.message || String(error),true);
}
async function action(button,fn) {
  if(button?.disabled) return;
  if(button) button.disabled=true;
  $('error-banner').hidden=true;
  try {return await fn();} catch(error) {showError(error);} finally {if(button) button.disabled=false;}
}
function go(view) {
  S.view=view;
  document.querySelectorAll('.view').forEach(x=>x.hidden=x.id!=='view-'+view);
  document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.view===view));
  const titles={overview:['Обзор','Контроль оборудования','Показания датчиков, состояние моделей и найденные отклонения.'],data:['Данные','Источники и показания','Загружайте измерения и управляйте наборами данных.'],models:['Модели','Модели обнаружения','Обучайте детекторы и сохраняйте версии экспериментов.'],history:['История запусков','История анализа','Возвращайтесь к результатам и сравнивайте проведённые проверки.'],system:['Состояние системы','Состояние системы','Проверяйте доступность сервисов и их взаимодействие.']};
  [$('breadcrumb').textContent,$('page-title').textContent,$('page-subtitle').textContent]=titles[view];
  if(view==='overview') setTimeout(()=>{for(const id of ['signal-chart','score-chart']) if($(id).data) Plotly.Plots.resize($(id));},30);
  if(view==='system') refreshSystem().catch(showError);
  if(view==='history') api('/runs').then(runs=>{S.runs=runs;renderLists();}).catch(showError);
  if(view==='models') api('/models').then(models=>{S.models=models;renderLists();}).catch(showError);
}
function populateSensors() {
  const sensors=currentDataset()?.sensors || [];
  if(!sensors.some(x=>x.sensor===S.sensor)) S.sensor=sensors[0]?.sensor || null;
  $('sensor-select').innerHTML=sensors.length ? sensors.map(x=>`<option value="${esc(x.sensor)}">${esc(sensorName(x.sensor))}</option>`).join('') : '<option value="">Нет данных</option>';
  $('sensor-select').value=S.sensor || '';
  if(S.sensor) localStorage.setItem('kontur-sensor',S.sensor);
}
function populateModels() {
  const compatible=S.models.filter(m=>m.status==='ready' && m.request.sensor===S.sensor && m.request.dataset_id===S.dataset);
  if(!compatible.some(m=>m.id===S.model)) S.model=compatible[0]?.id || null;
  $('model-select').innerHTML=compatible.length ? compatible.map(m=>`<option value="${m.id}">${esc(m.name)} · ${m.version}</option>`).join('') : '<option value="">Нет готовой модели</option>';
  $('model-select').value=S.model || '';
  const model=currentModel();
  $('model-details').innerHTML=model ? `<div class="detail-row"><span>Версия</span><strong>${model.version}</strong></div><div class="detail-row"><span>Окно</span><strong>${model.request.window} точек · ${(model.request.window*model.step_seconds/3600).toFixed(1)} ч</strong></div><div class="detail-row"><span>Лучшая эпоха</span><strong>${model.best_epoch} / ${model.request.epochs}</strong></div><div class="detail-row"><span>Обучена</span><strong>${date(model.finished_at)}</strong></div>` : '<p>Выберите датчик и обучите модель на периоде нормальной работы.</p>';
  $('analyze-button').disabled=!model;
}
function renderLists() {
  $('datasets-table').innerHTML=S.datasets.length ? S.datasets.map(d=>`<tr><td><strong>${esc(d.name)} ${d.id===S.dataset?'<span class="status-tag">Выбран</span>':''}</strong><small>${d.id.slice(0,8)} · ${d.labeled_rows? 'С разметкой':'Без разметки'} · пропуски: ${nf.format(d.missing_values||0)}</small></td><td>${nf.format(d.rows)}</td><td>${d.sensors.length}</td><td>${date(d.start)} — ${date(d.end)}</td><td><button class="button outline" data-dataset="${d.id}">Открыть</button></td></tr>`).join('') : '<tr><td colspan="5" class="table-empty">Добавьте демонстрационный набор или свой CSV.</td></tr>';
  $('models-list').innerHTML=S.models.length ? S.models.map(m=>`<article class="model-card"><div><h3>${esc(m.name)} <span class="status-tag ${m.status}">${statusText(m.status)}</span></h3><p>${esc(sensorName(m.request.sensor))} · версия ${m.version} · окно ${m.request.window} · ${m.epoch}/${m.request.epochs} эпох</p><p>Обучение ${date(m.request.train_start)} — ${date(m.request.train_end)} · порог до ${date(m.request.calibration_end)}</p>${m.error?`<p>${esc(m.error)}</p>`:''}${m.status==='ready'?`<p>Лучшая эпоха ${m.best_epoch} · ${m.fit_seconds.toFixed(1)} с · ${nf.format(m.train_windows)} обучающих окон</p>`:''}</div>${m.status==='ready'?`<button class="button outline" data-model="${m.id}">Использовать</button>`:''}</article>`).join('') : '<div class="table-empty">Сохранённых моделей пока нет.</div>';
  $('history-table').innerHTML=S.runs.length ? S.runs.map(r=>`<tr><td><strong>${date(r.created_at)}</strong><small>${new Date(r.created_at).toLocaleTimeString('ru-RU',{timeZone:'UTC'})}</small></td><td><strong>${esc(sensorName(r.sensor))}</strong><small>${esc(r.model_name)} · ${r.model_version}</small></td><td>${nf.format(r.anomaly_points)} <small>из ${nf.format(r.scored_points)} точек</small></td><td>${metric(r.metrics?.f1)}</td><td><button class="button outline" data-run="${r.id}">Открыть</button></td></tr>`).join('') : '<tr><td colspan="5" class="table-empty">Здесь появятся результаты выполненных анализов.</td></tr>';
  $('history-count').textContent=nf.format(S.runs.length)+' ЗАПУСКОВ';
  $('nav-models').textContent=S.models.length;
}
function renderStats() {
  const d=currentDataset();
  $('stat-rows').textContent=d?nf.format(d.rows):'—';
  $('stat-sensors').textContent=d?d.sensors.length:'—';
  $('stat-range').textContent=d?date(d.start)+' — '+date(d.end):'Загрузите данные для анализа';
  const ready=S.models.filter(m=>m.status==='ready' && m.request.dataset_id===S.dataset);
  $('stat-models').textContent=ready.length;
  $('stat-model-description').textContent=ready.length?'LSTM автоэнкодер · сохранённые версии':'Обучите первую модель';
  $('stat-anomalies').textContent=S.run?nf.format(S.run.anomaly_points):'—';
  $('stat-anomaly-description').textContent=S.run?`${(S.run.anomaly_points/S.run.scored_points*100).toFixed(1)}% проверенных точек`:'После выполнения анализа';
}
function clearResult() {
  S.run=null;
  if($('signal-chart').data) {
    Plotly.purge('signal-chart');
    $('signal-chart').innerHTML='<div class="chart-empty"><h3>Параметры анализа изменены</h3><p>Нажмите «Показать» для просмотра данных или «Найти аномалии» для расчёта.</p></div>';
    $('chart-point-count').textContent='Результат предыдущего анализа сброшен';
  }
  for(const id of ['metric-f1','metric-precision','metric-recall']) $(id).textContent='—';
  $('metric-note').textContent='Метрики появятся после анализа размеченных данных.';
  for(const id of ['report-link','csv-link']) {$(id).classList.add('disabled');$(id).href='#';}
  $('threshold-tag').textContent='ПОРОГ НЕ НАСТРОЕН';
  $('evaluation-note').hidden=true;
  if($('score-chart').data) Plotly.purge('score-chart');
  $('score-chart').innerHTML='<div class="small-empty">Выполните анализ, чтобы увидеть оценки.</div>';
  renderStats();
}
async function refresh({latest=false}={}) {
  const [sources,datasets,models,runs]=await Promise.all([api('/sources'),api('/datasets'),api('/models'),api('/runs')]);
  Object.assign(S,{sources,datasets,models,runs});
  if(!datasets.some(d=>d.id===S.dataset)) S.dataset=datasets[0]?.id || null;
  if(S.dataset) localStorage.setItem('kontur-dataset',S.dataset);
  populateSensors();populateModels();renderLists();renderStats();
  if(latest) {
    const previous=runs.find(r=>r.id===localStorage.getItem('kontur-run') && r.dataset_id===S.dataset) || runs.find(r=>r.dataset_id===S.dataset);
    if(previous) await loadRun(previous.id); else if(S.dataset) await preview();
  }
  const active=models.find(m=>['queued','training'].includes(m.status));
  if(active) pollTraining(active.id);
}
const plotConfig={responsive:true,displaylogo:false,scrollZoom:false,modeBarButtonsToRemove:['select2d','lasso2d','autoScale2d'],toImageButtonOptions:{format:'png',filename:'kontur-analysis',scale:2}};
function baseLayout(height) {
  return {height,margin:{l:48,r:20,t:14,b:32},paper_bgcolor:'transparent',plot_bgcolor:'#ffffff',font:{family:'Segoe UI, Arial',size:10,color:'#96a088'},showlegend:false,hovermode:'x unified',xaxis:{type:'date',tickformat:'%d.%m',hoverformat:'%d.%m.%Y %H:%M',gridcolor:'#f0f2eb',linecolor:'#e6ebdf',tickfont:{size:9},zeroline:false},yaxis:{gridcolor:'#eef1e7',zeroline:false,tickfont:{size:9},fixedrange:false},hoverlabel:{bgcolor:'#fff',bordercolor:'#dce4d1',font:{color:'#526848',size:11}}};
}
function labelShapes(points,step=600) {
  const shapes=[];let start=null,last=null;
  function close(){if(start!==null) shapes.push({type:'rect',xref:'x',yref:'paper',x0:start,x1:new Date(new Date(last).getTime()+step*1000).toISOString(),y0:0,y1:1,fillcolor:'#e8c5a0',opacity:.16,line:{width:0},layer:'below'});start=null;}
  for(const p of points){if(p.label===1){if(start===null)start=p.timestamp;last=p.timestamp;}else close();}close();return shapes;
}
async function draw(points,run=null) {
  if(!points.length) {
    if($('signal-chart').data) Plotly.purge('signal-chart');
    $('signal-chart').innerHTML='<div class="chart-empty"><h3>В этом периоде нет измерений</h3><p>Выберите даты в пределах загруженного набора.</p></div>';
    $('chart-point-count').textContent='Нет измерений в выбранном периоде';return;
  }
  if(!$('signal-chart').data) $('signal-chart').innerHTML='';
  const x=points.map(p=>p.timestamp);
  const traces=[{x,y:points.map(p=>p.value),type:'scatter',mode:'lines',name:'Измерения',line:{color:'#478e77',width:1.25},connectgaps:false,hovertemplate:'%{y:.3f} °C<extra>Измерения</extra>'}];
  if(run) {
    traces.push({x,y:points.map(p=>p.reconstructed),type:'scatter',mode:'lines',name:'Восстановление',line:{color:'#a29abe',width:1,dash:'dot'},connectgaps:false,hovertemplate:'%{y:.3f} °C<extra>Восстановление</extra>'});
    const flagged=points.filter(p=>p.prediction===1);
    traces.push({x:flagged.map(p=>p.timestamp),y:flagged.map(p=>p.value),type:'scatter',mode:'markers',name:'Аномалия',marker:{color:'#c87a60',size:3.5},hovertemplate:'%{y:.3f} °C<extra>Срабатывание модели</extra>'});
  }
  const signalLayout=baseLayout($('signal-chart').clientHeight || 325);signalLayout.shapes=labelShapes(points,run?.step_seconds||600);signalLayout.yaxis.title={text:'°C',font:{size:9},standoff:8};
  await Plotly.react('signal-chart',traces,signalLayout,plotConfig);
  $('chart-point-count').textContent=`${nf.format(points.length)} измерений${run?' · проверено '+nf.format(run.scored_points):''}`;
  $('chart-subtitle').textContent=sensorName(S.sensor)+' · '+date(points[0].timestamp)+' — '+date(points.at(-1).timestamp);
  if(run) {
    if(!$('score-chart').data) $('score-chart').innerHTML='';
    const scoreLayout=baseLayout($('score-chart').clientHeight||180);
    scoreLayout.margin.t=10;
    scoreLayout.shapes=[{type:'line',xref:'paper',x0:0,x1:1,yref:'y',y0:run.threshold,y1:run.threshold,line:{color:'#c49370',width:1.2,dash:'dash'}}];
    await Plotly.react('score-chart',[{x,y:points.map(p=>p.score),type:'scatter',mode:'lines',name:'Ошибка',fill:'tozeroy',fillcolor:'rgba(147,151,186,.08)',line:{color:'#9296b5',width:1.1},connectgaps:false,hovertemplate:'Ошибка: %{y:.4f}<extra></extra>'}],scoreLayout,plotConfig);
  }
}
async function preview() {
  if(!S.dataset||!S.sensor)return;
  const token=++S.plotToken;
  const query=new URLSearchParams({sensor:S.sensor,start:$('test-start').value,end:$('test-end').value});
  const result=await api(`/datasets/${S.dataset}/records?${query}`);
  if(token!==S.plotToken)return;
  clearResult();await draw(result.records);
}
async function selectDataset(id) {
  S.dataset=id;localStorage.setItem('kontur-dataset',id);S.sensor=null;S.model=null;
  clearResult();populateSensors();populateModels();renderStats();renderLists();
  const d=currentDataset();
  if(d?.start && (d.end.slice(0,10)<'2025-07-20'||d.start.slice(0,10)>'2025-09-09')) {
    $('test-start').value=d.start.slice(0,10);$('test-end').value=new Date(new Date(d.end).getTime()+86400000).toISOString().slice(0,10);
  }
  go('overview');await preview();
}
async function loadRun(id) {
  const result=await api('/runs/'+id);
  S.plotToken++;S.dataset=result.dataset_id;S.sensor=result.sensor;S.model=result.model_id;S.run=result;
  localStorage.setItem('kontur-dataset',S.dataset);localStorage.setItem('kontur-run',id);
  $('test-start').value=result.start.slice(0,10);$('test-end').value=result.end.slice(0,10);
  populateSensors();populateModels();renderLists();renderStats();
  $('metric-f1').textContent=metric(result.metrics?.f1);$('metric-precision').textContent=metric(result.metrics?.precision);$('metric-recall').textContent=metric(result.metrics?.recall);
  $('metric-note').textContent=result.metrics?`По ${nf.format(result.metrics.labeled_points)} размеченным точкам. Пропущено при анализе: ${nf.format(result.skipped_points)}.`:'Разметки нет. Число срабатываний не измеряет качество обнаружения.';
  $('threshold-tag').textContent='ПОРОГ '+result.threshold.toPrecision(3);
  $('evaluation-note').hidden=false;$('evaluation-note').querySelector('span').textContent=result.evaluation_note;
  for(const [key,path]of [['report-link','report'],['csv-link','csv']]){$(key).classList.remove('disabled');$(key).href=`/api/runs/${id}/${path}`;}
  go('overview');await draw(result.points,result);
}
async function importDemo(all=true,button=null) {
  await action(button,async()=>{
    if($('data-dialog').open)$('data-dialog').close();toast('Загружаем показания оборудования…');
    const d=currentDataset();const body={source_id:'iqds-demo',all_batches:all};
    if(d?.source_id==='iqds-demo' && d.source_offset>=(S.sources.find(s=>s.id==='iqds-demo')?.rows||0)) {
      await selectDataset(d.id);toast('Все демонстрационные данные уже загружены.');return;
    }
    if(d?.source_id==='iqds-demo' && d.source_offset<(S.sources.find(s=>s.id==='iqds-demo')?.rows||0))body.dataset_id=d.id;
    const result=await api('/import',{method:'POST',body});
    await refresh();await selectDataset(result.dataset.id);toast(`Загружено ${nf.format(result.loaded)} измерений`);
  });
}
function openTraining() {
  if(!S.dataset||!S.sensor){toast('Сначала загрузите данные и выберите датчик',true);return;}
  $('train-sensor-note').textContent=sensorName(S.sensor)+' · '+currentDataset().name;
  $('train-name').value='LSTM · '+sensorName(S.sensor).replace('Температура · ','');
  $('train-dialog').showModal();
}
async function pollTraining(id) {
  if(S.polling)return;S.polling=true;$('training-toast').hidden=false;
  let failures=0;
  try {
    while(true) {
      let model;
      try{model=await api('/models/'+id);failures=0;}catch(e){if(++failures>=5)throw e;await new Promise(r=>setTimeout(r,2000));continue;}
      $('training-title').textContent=model.name;
      $('training-progress').textContent=model.epoch?`Эпоха ${model.epoch} из ${model.request.epochs} · ${statusText(model.status).toLowerCase()}`:'Подготовка данных…';
      if(model.status==='ready') {
        S.models=await api('/models');S.model=id;populateModels();renderLists();renderStats();
        toast(`Модель обучена за ${model.fit_seconds.toFixed(1)} с. Можно запускать анализ.`);break;
      }
      if(['failed','interrupted'].includes(model.status)){await refresh();throw new Error(model.error||'Обучение прервано');}
      await new Promise(r=>setTimeout(r,1500));
    }
  }catch(error){showError(error);}finally{S.polling=false;$('training-toast').hidden=true;}
}
async function refreshSystem() {
  const statuses=await api('/system');
  let own;try{own=await fetch('/health').then(r=>r.json());}catch{own={service:'visualization',status:'unavailable'};}
  statuses.push(own);
  const okay=statuses.every(x=>x.status==='ok');
  $('system-pill').classList.toggle('offline',!okay);$('system-pill').innerHTML=`<i></i>${okay?'Все сервисы работают':'Проверьте сервисы'}`;
  const names={collector:['Collector','Сбор данных пакетами, журнал загрузок',8101],storage:['Storage','Измерения, результаты и история в SQLite',8102],ml_service:['ML Service','Обучение, версии моделей и анализ',8103],web_master:['Web Master','Выполнение пользовательских сценариев',8104],visualization:['Visualization','Интерфейс, графики и отчёты',8050]};
  $('services-list').innerHTML=statuses.map(x=>{const [name,desc,port]=names[x.service]||[x.service,'',0];return `<article class="service-card"><strong>${name}</strong><span class="status-tag ${x.status==='ok'?'ready':'failed'}">${x.status==='ok'?'Работает':'Недоступен'}</span><p>${desc}</p><a href="http://127.0.0.1:${port}/docs" target="_blank" rel="noopener">Документация API ↗</a></article>`;}).join('');
}

document.querySelectorAll('.nav-item').forEach(x=>x.addEventListener('click',()=>go(x.dataset.view)));
$('add-data').onclick=()=>$('data-dialog').showModal();
document.querySelectorAll('.close-dialog').forEach(x=>x.onclick=()=>x.closest('dialog').close());
$('load-demo').onclick=()=>importDemo(true,$('load-demo'));
$('dialog-demo').onclick=()=>importDemo(true,$('dialog-demo'));
$('next-batch').onclick=()=>importDemo(false,$('next-batch'));
for(const id of ['open-train','models-train'])$(id).onclick=openTraining;
document.querySelectorAll('[data-step]').forEach(x=>x.onclick=()=>{if(x.dataset.step==='data')$('data-dialog').showModal();if(x.dataset.step==='train')openTraining();if(x.dataset.step==='analyze'){go('overview');$('analyze-button').focus();toast('Выберите модель и нажмите «Найти аномалии».');}});
$('workflow-report').onclick=()=>{if(S.run)location.href=$('report-link').href;else toast('Сначала выполните анализ',true);};
$('preview-data').onclick=()=>action($('preview-data'),preview);
$('sensor-select').onchange=()=>action(null,async()=>{S.sensor=$('sensor-select').value;localStorage.setItem('kontur-sensor',S.sensor);S.model=null;populateModels();await preview();});
$('model-select').onchange=()=>action(null,async()=>{S.model=$('model-select').value;populateModels();await preview();});
for(const id of ['test-start','test-end'])$(id).onchange=()=>{S.plotToken++;clearResult();$('chart-subtitle').textContent='Период изменён. Нажмите «Показать» или «Найти аномалии».';};
$('analyze-button').onclick=()=>action($('analyze-button'),async()=>{
  if(!S.model)throw new Error('Выберите готовую модель');
  toast('Анализируем выбранный период…');
  const result=await api('/analyze',{method:'POST',body:{dataset_id:S.dataset,model_id:S.model,start:$('test-start').value,end:$('test-end').value}});
  S.runs=await api('/runs');await loadRun(result.id);toast('Анализ завершён. Отчёт и результаты сохранены.');
});
$('upload-form').onsubmit=event=>{event.preventDefault();action(event.submitter,async()=>{const file=$('csv-file').files[0];if(!file)throw new Error('Выберите CSV');if(file.size>15000000)throw new Error('Размер файла превышает 15 МБ');const form=new FormData();form.append('file',file);const d=await api('/upload',{method:'POST',body:form});$('data-dialog').close();await refresh();await selectDataset(d.id);toast('CSV загружен');});};
$('train-form').onsubmit=event=>{event.preventDefault();action(event.submitter,async()=>{const body={dataset_id:S.dataset,sensor:S.sensor,name:$('train-name').value,train_start:$('train-start').value,train_end:$('train-end').value,calibration_start:$('train-end').value,calibration_end:$('calibration-end').value,window:Number($('train-window').value),epochs:Number($('train-epochs').value)};const model=await api('/train',{method:'POST',body});$('train-dialog').close();clearResult();S.models=await api('/models');renderLists();renderStats();pollTraining(model.id);});};
$('datasets-table').onclick=event=>{const b=event.target.closest('[data-dataset]');if(b)action(b,()=>selectDataset(b.dataset.dataset));};
$('models-list').onclick=event=>{const b=event.target.closest('[data-model]');if(!b)return;action(b,async()=>{const m=S.models.find(x=>x.id===b.dataset.model);await selectDataset(m.request.dataset_id);S.sensor=m.request.sensor;S.model=m.id;populateSensors();populateModels();await preview();});};
$('history-table').onclick=event=>{const b=event.target.closest('[data-run]');if(b)action(b,()=>loadRun(b.dataset.run));};
$('refresh-system').onclick=()=>action($('refresh-system'),refreshSystem);
window.addEventListener('resize',()=>{for(const id of ['signal-chart','score-chart'])if($(id).data)Plotly.Plots.resize($(id));});
(async()=>{try{S.dataset=localStorage.getItem('kontur-dataset');S.sensor=localStorage.getItem('kontur-sensor');await refresh({latest:true});await refreshSystem();}catch(error){showError(error);}})();
