'use client';

import { useEffect, useRef, useState } from 'react';
import { useAuth } from '@/context/AuthContext';
import ReactMarkdown from 'react-markdown';
import MermaidBlock from '@/components/MermaidBlock';
import { getChatModels, getChatHistory, sendChatMessage } from '@/lib/api';
import { ChatMessage, ChatModel } from '@/lib/types';

interface ChatPanelProps {
  courseId: string;
  currentSectionPosition: number;
  currentSectionTitle: string;
}

export function ChatPanel({
  courseId,
  currentSectionPosition,
  currentSectionTitle,
}: ChatPanelProps) {
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
      // Use the first available model from the backend
      if (fetched.length > 0) {
        setSelectedModel(fetched[0].id);
      }
    }
    loadModels();
  }, []);

  // Auto-load history on mount (always visible, not toggled)
  useEffect(() => {
    if (!historyLoaded) {
      async function loadHistory() {
        const token = await getToken();
        const history = await getChatHistory(courseId, token);
        setMessages(history);
        setHistoryLoaded(true);
      }
      loadHistory();
    }
  }, [historyLoaded, courseId, getToken]);

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

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header bar with model picker */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border shrink-0">
        <span className="text-xs text-muted-foreground">
          Section {currentSectionPosition}
        </span>
        <div ref={modelPickerRef} className="relative">
          <button
            onClick={() => {
              setModelPickerOpen(!modelPickerOpen);
              setModelSearch('');
            }}
            className="flex items-center gap-1 px-2 py-0.5 text-xs bg-muted border border-border rounded-md hover:bg-accent transition-colors"
          >
            <span className="text-foreground">
              {formatModelName(selectedModel)}
            </span>
            <svg
              className="w-3 h-3 text-muted-foreground"
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
            <div className="absolute bottom-full right-0 mb-2 w-72 bg-popover border border-border rounded-lg shadow-xl overflow-hidden z-50">
              <div className="p-2 border-b border-border">
                <input
                  type="text"
                  value={modelSearch}
                  onChange={(e) => setModelSearch(e.target.value)}
                  placeholder="Search models..."
                  className="w-full px-2.5 py-1.5 text-sm bg-muted border border-border rounded text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-ring"
                  autoFocus
                />
              </div>
              <div className="max-h-60 overflow-y-auto">
                {filteredModels.length === 0 ? (
                  <div className="px-3 py-4 text-sm text-muted-foreground text-center">
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
                      className={`w-full text-left px-3 py-2 hover:bg-muted transition-colors ${
                        model.id === selectedModel ? 'bg-muted' : ''
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-foreground">
                          {formatModelName(model.id)}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {formatContextLength(model.context_length)} ctx
                        </span>
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        ${model.pricing_prompt} / ${model.pricing_completion}
                      </div>
                    </button>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
        {messages.length === 0 && !streaming && (
          <div className="flex items-center justify-center h-full">
            <p className="text-muted-foreground text-sm text-center px-4">
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
              <div className="max-w-[85%] px-3 py-2 bg-muted rounded-lg rounded-br-sm">
                <p className="text-sm text-foreground whitespace-pre-wrap">
                  {msg.content}
                </p>
              </div>
            ) : (
              <div className="max-w-[85%] prose prose-sm dark:prose-invert max-w-none text-foreground">
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
            <div className="max-w-[85%] prose prose-sm dark:prose-invert max-w-none text-foreground">
              {streamingContent ? (
                <ReactMarkdown components={markdownComponents}>
                  {streamingContent}
                </ReactMarkdown>
              ) : (
                <div className="flex items-center gap-1 py-2">
                  <span className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse" />
                  <span className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse [animation-delay:0.2s]" />
                  <span className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse [animation-delay:0.4s]" />
                </div>
              )}
              {streamingContent && (
                <span className="inline-block w-1.5 h-4 bg-primary animate-pulse ml-0.5 -mb-0.5" />
              )}
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input bar */}
      <div className="shrink-0 px-3 py-2 border-t border-border">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about this section..."
            rows={1}
            className="flex-1 resize-none px-3 py-2 text-sm bg-muted border border-border rounded-lg text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-ring max-h-24"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || streaming}
            className="px-3 py-2 bg-primary text-primary-foreground text-sm rounded-lg hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
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

// Backwards compatibility alias
export default ChatPanel;
