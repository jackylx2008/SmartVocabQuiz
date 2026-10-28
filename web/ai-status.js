const aiIndicator = document.getElementById('ai-status');
const aiLabel = aiIndicator.querySelector('.ai-label');
const aiDialog = document.getElementById('ai-required-dialog');
let pendingDestination = '';

function showAIState(state, label) {
  aiIndicator.className = `ai-indicator ${state}`;
  aiLabel.textContent = label;
  aiIndicator.title = `${label}；点击重新检测`;
  aiIndicator.setAttribute('aria-label', `${label}，点击重新检测`);
}

async function refreshAIStatus(force = false) {
  showAIState('checking', 'AI 检测中');
  try {
    const path = force ? '/api/ai-status?refresh=1' : '/api/ai-status';
    const response = await fetch(path, {cache: 'no-store'});
    if (!response.ok) throw new Error('状态检查失败');
    const data = await response.json();
    const available = data.available === true;
    showAIState(available ? 'online' : 'offline', available ? 'AI 已启动' : 'AI 未启动');
    return available;
  } catch {
    showAIState('offline', 'AI 未启动');
    return false;
  }
}

function showAIRequiredDialog() {
  if (aiDialog && !aiDialog.open) aiDialog.showModal();
}

for (const link of document.querySelectorAll('[data-requires-ai]')) {
  link.addEventListener('click', async (event) => {
    event.preventDefault();
    pendingDestination = link.href;
    if (await refreshAIStatus(true)) {
      window.location.assign(pendingDestination);
    } else {
      showAIRequiredDialog();
    }
  });
}

if (aiDialog) {
  document.getElementById('ai-dialog-close').addEventListener('click', () => aiDialog.close());
  document.getElementById('ai-dialog-cancel').addEventListener('click', () => aiDialog.close());
  document.getElementById('ai-dialog-retry').addEventListener('click', async () => {
    if (await refreshAIStatus(true)) {
      aiDialog.close();
      if (pendingDestination) window.location.assign(pendingDestination);
    }
  });
}

aiIndicator.addEventListener('click', () => refreshAIStatus(true));
refreshAIStatus();
setInterval(() => { if (!document.hidden) refreshAIStatus(); }, 10000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshAIStatus(true);
});
