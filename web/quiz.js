const generateButton = document.getElementById('generate-quiz');
const exportButton = document.getElementById('export-pdf');
const quizStatus = document.getElementById('quiz-status');
const quizPaper = document.getElementById('quiz-paper');
const quizForm = document.getElementById('quiz-form');
const candidateList = document.getElementById('candidate-list');
const exportStatus = document.getElementById('export-status');
let currentQuiz = null;
let candidates = [];
let pollTimer = null;
let reviewBusy = false;
let selectionBusy = false;

const typeNames = {
  T1_ODD_ONE_OUT: '找不同类', T2_READ_AND_MATCH: '特征选词',
  T4_MISSING_LETTERS: '补全字母', T5_UNSCRAMBLE_WORDS: '字母重组',
  T6_WORD_BANK_FILL: '词库填空', T8_PICTURE_WORD_CHOICE: '看图选词',
  T10_CN_EN_MATCH: '中英配对', T11_DIALOGUE_REPLY: '简短对话选答',
};

async function requestJson(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function postJson(path, payload) {
  return requestJson(path, {
    method: 'POST', headers: {'X-SmartVocabQuiz': '1', 'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
}

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function questionContent(question) {
  const box = element('div', 'question-content');
  const add = (className, value) => box.append(element('p', className, value));
  if (question.type === 'T1_ODD_ONE_OUT') {
    add('question-prompt', '圈出不同类的单词');
    add('review-options', question.options.join('   ·   '));
  } else if (question.type === 'T2_READ_AND_MATCH') {
    add('question-prompt', '根据特征选择对应的单词');
    add('question-sentence', question.clue);
    add('review-options', question.options.map((word, index) => `${String.fromCharCode(65 + index)}. ${word}`).join('   ·   '));
  } else if (question.type === 'T10_CN_EN_MATCH') {
    add('question-prompt', '选择对应的英文单词');
    add('question-sentence', question.cn_meaning);
    add('review-options', question.options.map((word, index) => `${String.fromCharCode(65 + index)}. ${word}`).join('   ·   '));
  } else if (question.type === 'T4_MISSING_LETTERS') {
    add('question-prompt', `根据中文补全字母：${question.cn_meaning}`);
    add('masked-word', question.masked_word);
  } else if (question.type === 'T5_UNSCRAMBLE_WORDS') {
    add('question-prompt', `看中文，重新组合字母：${question.cn_meaning}`);
    add('scrambled-word', question.scrambled);
  } else if (question.type === 'T6_WORD_BANK_FILL') {
    add('question-prompt', `词库：${question.word_bank.join(' · ')}`);
    add('question-prompt', question.cn_translation);
    add('question-sentence', question.sentence);
  } else if (question.type === 'T11_DIALOGUE_REPLY') {
    add('question-prompt', question.scene_cn);
    add('question-sentence', question.question);
    question.options.forEach((option, index) => add('review-choice', `${String.fromCharCode(65 + index)}. ${option}`));
  } else if (question.type === 'T8_PICTURE_WORD_CHOICE') {
    add('question-prompt', '根据图片选择单词');
    if (question.image_url) {
      const picture = element('img', 'quiz-picture');
      picture.src = question.image_url;
      picture.alt = '待审核题目图片';
      box.append(picture);
    }
    add('review-options', question.options.join('   ·   '));
  }
  add('review-answer', `参考答案：${question.answer}`);
  return box;
}

function renderQuestion(question) {
  const card = element('article', 'quiz-question review-question');
  card.dataset.number = String(question.number);
  card.append(element('span', 'question-number', String(question.number).padStart(2, '0')));
  const body = questionContent(question);
  const heading = element('div', 'review-question-heading');
  heading.append(element('strong', '', typeNames[question.type] || question.type));
  const approved = Boolean(currentQuiz.approved_ids[String(question.number)]);
  heading.append(element('span', approved ? 'review-state approved' : 'review-state',
    approved ? '已通过 · 已入备选题库' : '待审核'));
  body.prepend(heading);
  const actions = element('div', 'review-actions');
  const approve = element('button', 'review-approve', approved ? '已审核通过' : '审核通过');
  approve.type = 'button';
  approve.disabled = approved || reviewBusy;
  approve.addEventListener('click', () => reviewQuestion(question.number, 'approve'));
  const reject = element('button', 'review-reject', '不通过 · 重出此题');
  reject.type = 'button';
  reject.disabled = reviewBusy;
  reject.addEventListener('click', () => reviewQuestion(question.number, 'reject'));
  actions.append(approve, reject);
  body.append(actions);
  card.append(body);
  return card;
}

function renderQuiz(quiz, label = '已载入待审核试卷') {
  currentQuiz = quiz;
  quizForm.replaceChildren();
  for (const [index, section] of quiz.sections.entries()) {
    const block = element('section', 'quiz-section');
    const heading = element('div', 'quiz-section-heading');
    heading.append(element('span', 'section-index', `0${index + 1}`),
      element('h3', '', section.title), element('small', '', `${section.questions.length} 题`));
    block.append(heading);
    section.questions.forEach((question) => block.append(renderQuestion(question)));
    quizForm.append(block);
  }
  quizPaper.hidden = false;
  updateReviewCount();
  quizStatus.textContent = `${label} ${quiz.id}。`;
}

function updateReviewCount() {
  if (!currentQuiz) return;
  const total = currentQuiz.sections.reduce((sum, section) => sum + section.questions.length, 0);
  document.getElementById('review-count').textContent =
    `${Object.keys(currentQuiz.approved_ids).length} / ${total} 题已通过`;
}

async function loadReview(label) {
  try {
    renderQuiz(await requestJson('/api/quiz/review'), label);
    return true;
  } catch (_) {
    quizStatus.textContent = '还没有待审核试卷。点击“生成新试卷”开始。';
    return false;
  }
}

async function loadCandidates() {
  const data = await requestJson('/api/quiz/candidates');
  candidates = data.candidates;
  renderCandidates();
}

function renderCandidates() {
  candidateList.replaceChildren();
  if (!candidates.length) {
    candidateList.append(element('p', 'candidate-empty', '还没有通过审核的题目。先在上方逐题确认。'));
  }
  for (const candidate of candidates) {
    const row = element('label', 'candidate-row');
    const checkbox = element('input');
    checkbox.type = 'checkbox';
    checkbox.checked = Boolean(candidate.selected);
    checkbox.value = candidate.id;
    checkbox.disabled = reviewBusy || selectionBusy;
    checkbox.addEventListener('change', saveSelection);
    const details = element('span', 'candidate-details');
    details.append(element('strong', '', `${typeNames[candidate.question.type] || candidate.question.type} · 第 ${candidate.question_number} 题`));
    const summary = candidate.question.clue || candidate.question.scene_cn ||
      candidate.question.cn_meaning || candidate.question.cn_translation ||
      candidate.question.masked_word || candidate.question.scrambled ||
      candidate.question.options?.join(' · ') || candidate.question.answer;
    details.append(element('small', '', `${summary}  ·  答案：${candidate.question.answer}`));
    const digest = element('small', 'candidate-id', `ID ${candidate.id.slice(0, 12)}…`);
    digest.title = candidate.id;
    details.append(digest);
    row.append(checkbox, details);
    candidateList.append(row);
  }
  const count = candidates.filter((item) => item.selected).length;
  document.getElementById('selected-count').textContent = `${count} 题已选`;
  exportButton.disabled = count === 0 || reviewBusy || selectionBusy;
}

async function saveSelection() {
  if (selectionBusy) return;
  const selected = [...candidateList.querySelectorAll('input:checked')].map((input) => input.value);
  selectionBusy = true;
  exportButton.disabled = true;
  candidateList.querySelectorAll('input').forEach((input) => { input.disabled = true; });
  try {
    candidates = (await postJson('/api/quiz/candidates/select', {ids: selected})).candidates;
    exportStatus.textContent = '勾选已保存';
  } catch (error) {
    exportStatus.textContent = `保存勾选失败：${error.message}`;
    await loadCandidates();
  } finally {
    selectionBusy = false;
    renderCandidates();
  }
}

async function reviewQuestion(number, action) {
  if (!currentQuiz || reviewBusy) return;
  reviewBusy = true;
  renderQuiz(currentQuiz, action === 'reject' ? `正在重新生成第 ${number} 题…` : `正在保存第 ${number} 题…`);
  renderCandidates();
  try {
    const result = await postJson('/api/quiz/review', {quiz_id: currentQuiz.id, number, action});
    if (action === 'approve') {
      currentQuiz.approved_ids[String(number)] = result.candidate_id;
      quizStatus.textContent = `第 ${number} 题已通过审核并保存。`;
    } else {
      const section = currentQuiz.sections.find((group) => group.questions.some((item) => item.number === number));
      const index = section.questions.findIndex((item) => item.number === number);
      section.questions[index] = result.question;
      delete currentQuiz.approved_ids[String(number)];
      quizStatus.textContent = `第 ${number} 题已替换，请重新审核。`;
    }
    await loadCandidates();
  } catch (error) {
    quizStatus.textContent = `第 ${number} 题操作失败：${error.message}`;
  } finally {
    reviewBusy = false;
    const message = quizStatus.textContent;
    renderQuiz(currentQuiz);
    quizStatus.textContent = message;
    renderCandidates();
    quizForm.querySelector(`[data-number="${number}"]`)?.scrollIntoView({block: 'center'});
  }
}

async function watchGeneration() {
  if (pollTimer) return;
  generateButton.disabled = true;
  async function check() {
    try {
      const job = await requestJson('/api/quiz/status');
      quizStatus.textContent = job.message;
      if (job.state === 'running') return;
      clearInterval(pollTimer);
      pollTimer = null;
      generateButton.disabled = false;
      if (job.state === 'done') {
        await loadReview('新试卷已生成，请逐题审核');
      } else if (job.state === 'error') {
        const loaded = await loadReview('已载入上次试卷');
        quizStatus.textContent = `生成失败：${job.message}${loaded ? '；已载入上次试卷' : ''}`;
      }
    } catch (error) {
      clearInterval(pollTimer);
      pollTimer = null;
      generateButton.disabled = false;
      quizStatus.textContent = `状态检查失败：${error.message}`;
    }
  }
  pollTimer = setInterval(check, 1200);
  await check();
}

async function startGeneration(auto = false) {
  generateButton.disabled = true;
  if (auto) quizPaper.hidden = true;
  quizStatus.textContent = auto ? '本次启动正在自动生成待审核试卷…' : '正在启动出题任务…';
  try {
    await postJson('/api/quiz/generate', {});
    await watchGeneration();
  } catch (error) {
    generateButton.disabled = false;
    try {
      if ((await requestJson('/api/quiz/status')).state === 'running') {
        await watchGeneration();
        return;
      }
    } catch (_) { /* 保留原始错误。 */ }
    const loaded = auto ? await loadReview('已载入上次试卷') : false;
    quizStatus.textContent = `无法生成：${error.message}${loaded ? '；已载入上次试卷' : ''}`;
  }
}

generateButton.addEventListener('click', () => startGeneration());
exportButton.addEventListener('click', async () => {
  exportButton.disabled = true;
  exportStatus.textContent = '正在生成 A4 横版 PDF…';
  try {
    const result = await postJson('/api/quiz/export', {});
    const link = element('a', '', `下载 ${result.filename}`);
    link.href = `/download/quiz-pdf?name=${encodeURIComponent(result.filename)}`;
    link.download = result.filename;
    exportStatus.replaceChildren(element('span', '', '已保存到 output/ · '), link);
    link.click();
  } catch (error) {
    exportStatus.textContent = `导出失败：${error.message}`;
  } finally {
    exportButton.disabled = candidates.filter((item) => item.selected).length === 0;
  }
});

loadCandidates().catch((error) => { exportStatus.textContent = `读取备选题失败：${error.message}`; });
requestJson('/api/quiz/status').then((job) => {
  if (job.state === 'running') watchGeneration();
  else if (job.auto_generate_pending) startGeneration(true);
  else loadReview('已载入本次待审核试卷');
}).catch(() => { quizStatus.textContent = '无法连接本地试题服务。'; });
