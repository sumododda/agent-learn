'use client';

import { useEffect, useRef, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import ReactMarkdown from 'react-markdown';
import MermaidBlock from '@/components/MermaidBlock';
import { getChatModels, getChatHistory, sendChatMessage } from '@/lib/api';
import { ChatMessage, ChatModel } from '@/lib/types';

interface ChatDrawerProps {
  courseId: string;
  currentSectionPosition: number;
  currentSectionTitle: string;
}

export default function ChatDrawer({
  courseId,
  currentSectionPosition,
  currentSectionTitle,
}: ChatDrawerProps) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState('');
  const [models, setModels] = useState<ChatModel[]>([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [modelSearch, setModelSearch] = useState('');
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const { getToken } = useAuth();

  // Load models on mount
  useEffect(() => {
    async function loadModels() {
      const fetched = await getChatModels();
      setModels(fetched);
      const defaultModel =
        process.env.NEXT_PUBLIC_CHAT_DEFAULT_MODEL || 'anthropic/claude-sonnet-4';
      // If the default model exists in the list, use it; otherwise use first available
      if (fetched.some((m) => m.id === defaultModel)) {
        setSelectedModel(defaultModel);
      } else if (fetched.length > 0) {
        setSelectedModel(fetched[0].id);
      } else {
        setSelectedModel(defaultModel);
      }
    }
    loadModels();
  }, []);

  // Load history when drawer opens
  useEffect(() => {
    if (open && !historyLoaded) {
      async function loadHistory() {
        const token = await getToken();
        const history = await getChatHistory(courseId, token);
        setMessages(history);
        setHistoryLoaded(true);
      }
      loadHistory();
    }
  }, [open, historyLoaded, courseId, getToken]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

  // Close model picker on click outside
  useEffect(() => {
    if (!modelPickerOpen) return;
    function handleClickOutside(e: MouseEvent) {
      if (
        modelPickerRef.current &&
        !modelPickerRef.current.contains(e.target as Node)
      ) {
        setModelPickerOpen(false);
        setModelSearch('');
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [modelPickerOpen]);

  async function handleSend() {
    if (!input.trim() || streaming) return;

    const userMessage: ChatMessage = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: input.trim(),
      model: null,
      section_context: currentSectionPosition,
      created_at: new Date().toISOString(),
    };

    const messageText = input.trim();
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setStreaming(true);
    setStreamingContent('');

    try {
      const token = await getToken();
      const response = await sendChatMessage(
        courseId,
        messageText,
        selectedModel,
        currentSectionPosition,
        token
      );

      if (!response.ok || !response.body) {
        setStreaming(false);
        const errorMsg: ChatMessage = {
          id: `err-${Date.now()}`,
          role: 'assistant',
          content: 'Failed to get a response. Please try again.',
          model: selectedModel,
          section_context: currentSectionPosition,
          created_at: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, errorMsg]);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let fullContent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith('data: ')) {
            const data = trimmed.slice(6);
            if (data === '[DONE]') continue;
            try {
              const parsed = JSON.parse(data);
              const content = parsed.choices?.[0]?.delta?.content;
              if (content) {
                fullContent += content;
                setStreamingContent((prev) => prev + content);
              }
            } catch {
              // Ignore malformed SSE chunks
            }
          }
        }
      }

      const assistantMessage: ChatMessage = {
        id: `asst-${Date.now()}`,
        role: 'assistant',
        content: fullContent,
        model: selectedModel,
        section_context: currentSectionPosition,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch {
      const errorMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        role: 'assistant',
        content: 'An error occurred. Please try again.',
        model: selectedModel,
        section_context: currentSectionPosition,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setStreaming(false);
      setStreamingContent('');
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function formatModelName(modelId: string): string {
    const parts = modelId.split('/');
    return parts.length > 1 ? parts[parts.length - 1] : modelId;
  }

  function formatContextLength(length: number): string {
    if (length >= 1000) {
      return `${Math.round(length / 1000)}k`;
    }
    return String(length);
  }

  const filteredModels = models.filter((m) => {
    if (!modelSearch) return true;
    const search = modelSearch.toLowerCase();
    return (
      m.id.toLowerCase().includes(search) ||
      m.name.toLowerCase().includes(search)
    );
  });

  const markdownComponents = {
    code({ className, children }: { className?: string; children?: React.ReactNode }) {
      if (/language-mermaid/.test(className || '')) {
        return (
          <MermaidBlock
            definition={String(children).replace(/\n$/, '')}
          />
        );
      }
      return <code className={className}>{children}</code>;
    },
  };

  // Collapsed state — floating pill button
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-4 right-6 z-50 flex items-center gap-2 px-4 py-2.5 bg-gray-900 border border-gray-700 rounded-full shadow-lg hover:bg-gray-800 transition-colors"
      >
        <svg
          className="w-4 h-4 text-purple-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
          />
        </svg>
        <span className="text-sm text-white font-medium">Ask AI</span>
        <span className="text-xs text-gray-500">
          {formatModelName(selectedModel)}
        </span>
      </button>
    );
  }

  // Expanded state — bottom drawer
  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 h-[40vh] bg-gray-950 border-t border-gray-800 flex flex-col">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800 flex-shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-white">Assistant</span>
          <span className="text-xs text-gray-500">
            reading section {currentSectionPosition}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* Model chip */}
          <div ref={modelPickerRef} className="relative">
            <button
              onClick={() => {
                setModelPickerOpen(!modelPickerOpen);
                setModelSearch('');
              }}
              className="flex items-center gap-1.5 px-2.5 py-1 text-xs bg-gray-800 border border-gray-700 rounded-full hover:bg-gray-700 transition-colors"
            >
              <span className="text-gray-300">
                {formatModelName(selectedModel)}
              </span>
              <svg
                className="w-3 h-3 text-gray-500"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19 9l-7 7-7-7"
                />
              </svg>
            </button>

            {/* Model picker dropdown */}
            {modelPickerOpen && (
              <div className="absolute bottom-full right-0 mb-2 w-80 bg-gray-900 border border-gray-700 rounded-lg shadow-xl overflow-hidden">
                <div className="p-2 border-b border-gray-800">
                  <input
                    type="text"
                    value={modelSearch}
                    onChange={(e) => setModelSearch(e.target.value)}
                    placeholder="Search models..."
                    className="w-full px-3 py-1.5 text-sm bg-gray-800 border border-gray-700 rounded text-white placeholder-gray-500 focus:outline-none focus:border-purple-500"
                    autoFocus
                  />
                </div>
                <div className="max-h-60 overflow-y-auto">
                  {filteredModels.length === 0 ? (
                    <div className="px-3 py-4 text-sm text-gray-500 text-center">
                      No models found
                    </div>
                  ) : (
                    filteredModels.map((model) => (
                      <button
                        key={model.id}
                        onClick={() => {
                          setSelectedModel(model.id);
                          setModelPickerOpen(false);
                          setModelSearch('');
                        }}
                        className={`w-full text-left px-3 py-2 hover:bg-gray-800 transition-colors ${
                          model.id === selectedModel ? 'bg-gray-800' : ''
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-sm text-white">
                            {formatModelName(model.id)}
                          </span>
                          <span className="text-xs text-gray-500">
                            {formatContextLength(model.context_length)} ctx
                          </span>
                        </div>
                        <div className="text-xs text-gray-500 mt-0.5">
                          ${model.pricing_prompt} / ${model.pricing_completion}
                        </div>
                      </button>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Close button */}
          <button
            onClick={() => setOpen(false)}
            className="p-1 text-gray-500 hover:text-white transition-colors"
          >
            <svg
              className="w-4 h-4"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M19 9l-7 7-7-7"
              />
            </svg>
          </button>
        </div>
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !streaming && (
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 text-sm">
              Ask a question about &ldquo;{currentSectionTitle}&rdquo;
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            {msg.role === 'user' ? (
              <div className="max-w-[80%] px-3 py-2 bg-gray-800 rounded-lg rounded-br-sm">
                <p className="text-sm text-white whitespace-pre-wrap">
                  {msg.content}
                </p>
              </div>
            ) : (
              <div className="max-w-[80%] prose prose-invert prose-sm prose-purple max-w-none">
                <ReactMarkdown components={markdownComponents}>
                  {msg.content}
                </ReactMarkdown>
              </div>
            )}
          </div>
        ))}

        {/* Streaming message */}
        {streaming && (
          <div className="flex justify-start">
            <div className="max-w-[80%] prose prose-invert prose-sm prose-purple max-w-none">
              {streamingContent ? (
                <ReactMarkdown components={markdownComponents}>
                  {streamingContent}
                </ReactMarkdown>
              ) : (
                <div className="flex items-center gap-1 py-2">
                  <span className="w-1.5 h-1.5 bg-purple-400 rounded-full animate-pulse" />
                  <span className="w-1.5 h-1.5 bg-purple-400 rounded-full animate-pulse [animation-delay:0.2s]" />
                  <span className="w-1.5 h-1.5 bg-purple-400 rounded-full animate-pulse [animation-delay:0.4s]" />
                </div>
              )}
              {streamingContent && (
                <span className="inline-block w-1.5 h-4 bg-purple-400 animate-pulse ml-0.5 -mb-0.5" />
              )}
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input bar */}
      <div className="flex-shrink-0 px-4 py-3 border-t border-gray-800">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about this section..."
            rows={1}
            className="flex-1 resize-none px-3 py-2 text-sm bg-gray-900 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 max-h-24"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || streaming}
            className="px-3 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <svg
              className="w-4 h-4"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 19V5m0 0l-7 7m7-7l7 7"
              />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
