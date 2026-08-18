import { Wllama } from '@wllama/wllama';

const noopCacheManager = {
    download: async () => {},
    open: async () => null,
    getSize: async () => -1,
    list: async () => [],
    clear: async () => {},
    delete: async () => {},
    getNameFromURL: async (url) => url,
};

function startFakeProgress(onProgress, target = 0.95, interval = 120) {
    let value = 0;
    onProgress(0, '初始化运行时...');
    const timer = setInterval(() => {
        value += (target - value) * 0.06;
        onProgress(value, '加载模型权重...');
    }, interval);
    return () => {
        clearInterval(timer);
        onProgress(1, '加载完成');
    };
}

export class LLM {
    constructor(wasmPath = './esm/wasm/wllama.wasm', options = {}) {
        this.wllama = new Wllama(
            { "default": wasmPath },
            {
                logger: options.logger ?? console,
                suppressNativeLog: options.suppressNativeLog ?? false,
                cacheManager: noopCacheManager,
            }
        );
        this.modelLoaded = false;
        this.modelName = '';
    }

    isModelLoaded() {
        return this.modelLoaded;
    }

    getModelName() {
        return this.modelName;
    }

    async loadModel(file, options = {}) {
        const fileArr = Array.isArray(file) ? file : [file];
        const maxSize = options.maxSize ?? (2 * 1024 ** 3);
        if (fileArr[0] && fileArr[0].size > maxSize) {
            throw new Error(`模型文件过大（>${(maxSize / 1024 ** 3).toFixed(0)}GB）`);
        }

        const onProgress = options.onProgress ?? (() => {});
        const isFile = fileArr[0] instanceof Blob;
        let stopFake = null;

        const loadParams = {
            reasoning: true,
            reasoning_budget_tokens: -1,
        };

        if (isFile) {
            stopFake = startFakeProgress(onProgress);
        } else {
            loadParams.progressCallback = ({ loaded, total }) => {
                if (total > 0) onProgress(loaded / total, '下载模型...');
            };
        }

        try {
            await this.wllama.loadModel(fileArr, loadParams);
            this.modelLoaded = true;
            this.modelName = fileArr[0]?.name ?? '';
        } finally {
            if (stopFake) stopFake();
        }
    }

    async chat(messages, callbacks = {}, options = {}) {
        const {
            onReasoning = () => {},
            onContent = () => {},
            onFirstToken = () => {},
        } = callbacks;

        const thinking = options.thinking !== false;
        let firstTokenEmitted = false;
        let reasoningDone = false;

        const emitFirst = (type) => {
            if (!firstTokenEmitted) {
                firstTokenEmitted = true;
                onFirstToken(type);
            }
        };

        await this.wllama.createChatCompletion({
            messages,
            max_tokens: options.max_tokens ?? 256,
            temperature: options.temperature ?? 0.7,
            stream: true,
            chat_template_kwargs: { thinking },
            onData: (chunk) => {
                const delta = chunk.choices?.[0]?.delta;
                if (!delta) return;

                const reasoningChunk =
                    delta.reasoning_content ?? delta.reasoning ?? delta.thinking;
                if (typeof reasoningChunk === 'string' && reasoningChunk.length > 0) {
                    emitFirst('reasoning');
                    onReasoning(reasoningChunk);
                }
                if (typeof delta.content === 'string' && delta.content.length > 0) {
                    if (!reasoningDone) {
                        reasoningDone = true;
                        onReasoning(null);
                    }
                    emitFirst('content');
                    onContent(delta.content);
                }
            },
        });
    }

    async exit() {
        try {
            await this.wllama.exit();
        } finally {
            this.modelLoaded = false;
        }
    }
}