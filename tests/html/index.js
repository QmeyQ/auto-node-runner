import { LLM } from './llm.js';
import { ChatHistory } from './chat-history.js';

const llm = new LLM();
const history = new ChatHistory({ storageKey: 'wllama_chat_history' });

const modelFileInput = document.getElementById('modelFile');
const systemPromptTextarea = document.getElementById('systemPrompt');
const promptTextarea = document.getElementById('prompt');
const outputTextarea = document.getElementById('output');
const historyTextarea = document.getElementById('history');
const runBtn = document.getElementById('runBtn');
const clearHistoryBtn = document.getElementById('clearHistoryBtn');
const thinkCheckbox = document.getElementById('thinkCheckbox');
const statusDiv = document.getElementById('status');
const speedDiv = document.getElementById('speed');

let reasoningText = '';
let contentText = '';
let isReasoningActive = false;

function renderOutput() {
    let result = '';
    if (reasoningText) {
        const label = isReasoningActive ? '思考过程（进行中）' : '思考过程';
        result += `—— ${label} ——\n${reasoningText}\n\n—— 生成结果 ——\n`;
    }
    result += contentText;
    outputTextarea.value = result;
    outputTextarea.scrollTop = outputTextarea.scrollHeight;
}

function renderHistory() {
    historyTextarea.value = history.toDisplayString();
    historyTextarea.scrollTop = historyTextarea.scrollHeight;
    clearHistoryBtn.disabled = history.isEmpty();
}

function setRunBtn(state, text) {
    const states = {
        idle: { disabled: true, text: '⏳ 请先加载模型' },
        loading: { disabled: true, text: '⏳ 加载模型中...' },
        ready: { disabled: false, text: '🚀 推理' },
        running: { disabled: true, text: '⏳ 响应中...' },
        done: { disabled: false, text: '🚀 推理' },
    };
    const s = states[state] ?? states.idle;
    runBtn.disabled = s.disabled;
    runBtn.textContent = text ?? s.text;
}

function buildMessages(userPrompt, thinking) {
    const messages = [];
    const sysRaw = systemPromptTextarea.value.trim();
    let sysContent = sysRaw;
    if (!thinking) {
        sysContent = sysContent ? `${sysContent} /no_think` : '/no_think';
    }
    if (sysContent) {
        messages.push({ role: 'system', content: sysContent });
    }
    for (const m of history.getAll()) {
        messages.push(m);
    }
    messages.push({ role: 'user', content: userPrompt });
    return messages;
}

modelFileInput.addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    if (file.size > 2 * 1024 ** 3) {
        statusDiv.textContent = '❌ 模型文件过大（>2GB）';
        return;
    }

    if (llm.isModelLoaded()) {
        statusDiv.textContent = '⏳ 正在卸载旧模型...';
        try {
            await llm.exit();
        } catch (e) {
            console.warn('卸载旧模型失败：', e);
        }
    }

    statusDiv.textContent = `⏳ 加载模型 "${file.name}" ...`;
    setRunBtn('loading');
    outputTextarea.value = '';
    speedDiv.textContent = '响应速度：—';

    try {
        await llm.loadModel(file, {
            onProgress: (p, stage) => {
                statusDiv.textContent = `⏳ ${stage ?? '加载中'} ${Math.round(p * 100)}%`;
            },
        });
        setRunBtn('ready');
        statusDiv.textContent = `✅ 模型加载完成：${llm.getModelName()}`;
    } catch (error) {
        console.error(error);
        statusDiv.textContent = `❌ 加载失败: ${error.message}`;
        setRunBtn('idle');
    }
});

clearHistoryBtn.addEventListener('click', () => {
    if (!confirm('确定清除所有对话历史吗？')) return;
    history.clear();
    renderHistory();
    statusDiv.textContent = '已清除对话历史';
});

runBtn.addEventListener('click', async () => {
    if (!llm.isModelLoaded()) { alert('请先加载模型！'); return; }
    const prompt = promptTextarea.value.trim();
    if (!prompt) { alert('请输入提示词！'); return; }

    const thinking = thinkCheckbox.checked;
    const messages = buildMessages(prompt, thinking);

    reasoningText = '';
    contentText = '';
    isReasoningActive = false;
    outputTextarea.value = '';
    setRunBtn('running');
    statusDiv.textContent = '⏳ 推理中 ...';
    speedDiv.textContent = '响应速度：等待响应...';

    const startTime = performance.now();
    let firstTokenTime = null;

    try {
        await llm.chat(
            messages,
            {
                onReasoning: (text) => {
                    if (text === null) {
                        isReasoningActive = false;
                    } else {
                        if (!isReasoningActive) isReasoningActive = true;
                        reasoningText += text;
                    }
                    renderOutput();
                },
                onContent: (text) => {
                    contentText += text;
                    renderOutput();
                },
                onFirstToken: (type) => {
                    firstTokenTime = performance.now();
                    const elapsed = (firstTokenTime - startTime) / 1000;
                    const typeLabel = type === 'reasoning' ? '思考' : '内容';
                    speedDiv.textContent = `响应速度：首词用时 ${elapsed.toFixed(3)} 秒（${typeLabel}）`;
                    statusDiv.textContent = `⏳ 响应中（${typeLabel}）...`;
                },
            },
            { max_tokens: 512, temperature: 0.7, thinking }
        );

        history.addUser(prompt);
        history.addAssistant(contentText, reasoningText);
        renderHistory();

        setRunBtn('done');
        statusDiv.textContent = '✅ 推理完成';
        if (firstTokenTime) {
            const total = (performance.now() - startTime) / 1000;
            speedDiv.textContent += ` | 总耗时 ${total.toFixed(3)} 秒`;
        }
    } catch (error) {
        console.error(error);
        statusDiv.textContent = `❌ 推理失败: ${error.message}`;
        outputTextarea.value += `\n\n[错误] ${error.message}`;
        setRunBtn('ready');
    }
});

renderHistory();
statusDiv.textContent = '请选择 .gguf 模型文件开始。';
