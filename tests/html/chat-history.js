export class ChatHistory {
    constructor(options = {}) {
        this.messages = [];
        this.storageKey = options.storageKey ?? null;
        this.maxMessages = options.maxMessages ?? 200;
        if (this.storageKey) this.restore();
    }

    add(message) {
        this.messages.push(message);
        while (this.messages.length > this.maxMessages) {
            this.messages.shift();
        }
        this.persist();
    }

    addUser(content) {
        this.add({ role: 'user', content });
    }

    addAssistant(content, reasoning = '') {
        this.add({ role: 'assistant', content, reasoning });
    }

    clear() {
        this.messages = [];
        this.persist();
    }

    size() {
        return this.messages.length;
    }

    isEmpty() {
        return this.messages.length === 0;
    }

    getAll() {
        return this.messages.map(({ role, content }) => ({ role, content }));
    }

    toDisplayString() {
        if (this.messages.length === 0) return '（暂无对话历史）';
        return this.messages.map((m, i) => {
            const tag = m.role === 'user' ? '👤 用户' : '🤖 助手';
            let s = `[${i + 1}] ${tag}：\n${m.content}`;
            if (m.role === 'assistant' && m.reasoning) {
                s += `\n   💭 思考：${m.reasoning}`;
            }
            return s;
        }).join('\n\n────────────────────\n\n');
    }

    persist() {
        if (!this.storageKey) return;
        try {
            localStorage.setItem(this.storageKey, JSON.stringify(this.messages));
        } catch (_) {}
    }

    restore() {
        if (!this.storageKey) return;
        try {
            const raw = localStorage.getItem(this.storageKey);
            if (raw) this.messages = JSON.parse(raw) ?? [];
        } catch (_) {
            this.messages = [];
        }
    }
}