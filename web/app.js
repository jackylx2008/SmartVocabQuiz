const $ = (id) => document.getElementById(id);
let vocabulary = [];
let lastVocabularyCount = -1;
let lastJobState = '';
let uploading = false;
let notification = '';

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderFiles(files) {
  const list = $('file-list');
  list.replaceChildren();
  $('file-count').textContent = `${files.length} 份`;
  if (!files.length) {
    list.append(makeElement('p', 'empty-state', 'BookScan 中还没有 PDF。'));
    return;
  }
  for (const file of files) {
    const row = makeElement('div', 'file-row');
    row.append(makeElement('span', 'file-icon', 'PDF'));
    const copy = makeElement('div', 'file-name');
    copy.append(makeElement('strong', '', file.name));
    copy.append(makeElement('small', '', `SHA-256  ${file.id.slice(0, 14)}…`));
    row.append(copy);
    row.append(makeElement('span', `file-status ${file.status}`, file.status === 'scanned' ? '已扫描' : '待扫描'));
    list.append(row);
  }
}

function renderVocabulary() {
  const query = $('search-input').value.trim().toLocaleLowerCase();
  const matches = vocabulary.filter((item) =>
    item.english.toLocaleLowerCase().includes(query) || item.chinese.toLocaleLowerCase().includes(query));
  const body = $('vocab-body');
  body.replaceChildren();
  for (const item of matches) {
    const row = document.createElement('tr');
    row.append(makeElement('td', '', item.english));
    row.append(makeElement('td', '', item.chinese));
    const sources = new Set(item.sources.map((source) => source.file));
    row.append(makeElement('td', '', `${sources.size} 份 PDF`));
    body.append(row);
  }
  if (!matches.length) {
    const row = document.createElement('tr');
    const cell = makeElement('td', '', query ? '没有找到匹配的词条。' : '词库还没有词条。');
    cell.colSpan = 3;
    row.append(cell);
    body.append(row);
  }
  $('vocab-summary').textContent = query ? `找到 ${matches.length} 项 · 词库共 ${vocabulary.length} 项` : `显示全部 ${vocabulary.length} 项`;
}

async function loadVocabulary() {
  const response = await fetch('/api/vocabulary');
  if (!response.ok) throw new Error('读取词库失败');
  const data = await response.json();
  vocabulary = Array.isArray(data.entries) ? data.entries : [];
  lastVocabularyCount = vocabulary.length;
  renderVocabulary();
}

async function refreshStatus() {
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw new Error('读取扫描状态失败');
    const data = await response.json();
    $('stat-entries').textContent = data.counts.entry_count;
    $('stat-scanned').textContent = data.counts.scanned;
    $('stat-pending').textContent = data.counts.pending;
    renderFiles(data.pdfs);
    const state = data.job.state;
    const labels = {idle: '就绪', running: '扫描中', done: '已完成', error: '需要处理'};
    $('job-pill').className = `status-pill ${state}`;
    $('job-pill').textContent = labels[state] || state;
    $('job-message').textContent = notification || data.job.message;
    $('progress-fill').className = state === 'running' || state === 'done' ? state : '';
    $('scan-button').disabled = state === 'running';
    $('upload-button').disabled = state === 'running' || uploading;
    $('scan-button').textContent = state === 'running' ? '正在扫描…' : '✦  扫描新增 PDF';
    if (data.counts.entry_count !== lastVocabularyCount || (lastJobState === 'running' && state === 'done')) {
      await loadVocabulary();
    }
    lastJobState = state;
  } catch (error) {
    $('job-pill').className = 'status-pill error';
    $('job-pill').textContent = '连接失败';
    $('job-message').textContent = error.message;
  }
}

async function startScan() {
  notification = '';
  $('scan-button').disabled = true;
  try {
    const response = await fetch('/api/scan', {method: 'POST', headers: {'X-SmartVocabQuiz': '1'}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '无法启动扫描');
    await refreshStatus();
  } catch (error) {
    notification = error.message;
    $('job-message').textContent = error.message;
    $('scan-button').disabled = false;
  }
}

async function uploadFiles(files) {
  if (!files.length) return;
  notification = '';
  uploading = true;
  $('upload-button').disabled = true;
  try {
    for (const file of files) {
      $('job-message').textContent = `正在添加 ${file.name}…`;
      const response = await fetch('/api/upload', {
        method: 'POST',
        headers: {'X-SmartVocabQuiz': '1', 'X-Filename': encodeURIComponent(file.name), 'Content-Type': 'application/pdf'},
        body: file,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `添加 ${file.name} 失败`);
    }
    await refreshStatus();
    await startScan();
  } catch (error) {
    notification = error.message;
    $('job-message').textContent = error.message;
  } finally {
    uploading = false;
    $('upload-input').value = '';
    await refreshStatus();
  }
}

$('scan-button').addEventListener('click', startScan);
$('upload-button').addEventListener('click', () => $('upload-input').click());
$('upload-input').addEventListener('change', (event) => uploadFiles(Array.from(event.target.files)));
$('search-input').addEventListener('input', renderVocabulary);
refreshStatus();
setInterval(refreshStatus, 2000);
