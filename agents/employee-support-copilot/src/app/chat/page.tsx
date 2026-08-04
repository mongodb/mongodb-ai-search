"use client";

import { useEffect, useRef, useState } from "react";
import ChatInput from "../../components/ChatInput";
import ChatMessage from "../../components/ChatMessage";
import type { ChatMessage as ChatMessageType, ChatRequest, ChatResponse } from "../../types/chat";
import type { DomainKey } from "../../lib/collections";

function generateId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function ThinkingIndicator() {
  return (
    <div className="flex justify-start mb-4">
      <div className="rounded-2xl rounded-tl-sm bg-white border border-gray-200 px-4 py-3 shadow-sm">
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-gray-400 animate-bounce [animation-delay:-0.3s]" />
          <span className="w-2 h-2 rounded-full bg-gray-400 animate-bounce [animation-delay:-0.15s]" />
          <span className="w-2 h-2 rounded-full bg-gray-400 animate-bounce" />
        </div>
      </div>
    </div>
  );
}

const WELCOME: ChatMessageType = {
  id: "welcome",
  role: "assistant",
  content: "",
  timestamp: new Date(),
  response: {
    answer:
      "Hi! I'm your **Employee Support Copilot** 👋\n\n" +
      "I can help you with:\n\n" +
      "💻 **IT issues** — VPN, laptop, MFA, SSO, software, device setup\n\n" +
      "🧑‍💼 **HR & Employee support** — leave policy, payroll, travel, benefits, onboarding\n\n" +
      "Just ask your question below. I'll automatically route it to the right knowledge base.",
    citations: [],
    routing: {
      domain: "employee_support",
      domainLabel: "Employee Support Copilot",
      confidence: 1,
      ambiguous: false,
      isDualCollection: false,
      strategy: "welcome",
      bias: "auto",
      matchedSignals: [],
    },
    timings: { classificationMs: 0, searchaasMs: 0, totalMs: 0 },
    hasSummary: false,
  },
};

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessageType[]>([WELCOME]);
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function handleSend(query: string, forceDomain?: DomainKey) {
    const userMsg: ChatMessageType = {
      id: generateId(),
      role: "user",
      content: query,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    let assistantMsg: ChatMessageType;
    try {
      const body: ChatRequest = { query, forceDomain, topK: 8 };
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = (await res.json()) as ChatResponse;

      assistantMsg = {
        id: generateId(),
        role: "assistant",
        content: data.answer,
        response: data,
        timestamp: new Date(),
      };
    } catch {
      assistantMsg = {
        id: generateId(),
        role: "assistant",
        content:
          "Sorry, I couldn't reach the search service. Please check your connection and try again.",
        timestamp: new Date(),
        response: {
          answer: "Sorry, I couldn't reach the search service. Please check your connection and try again.",
          citations: [],
          routing: {
            domain: "employee_support",
            domainLabel: "Error",
            confidence: 0,
            ambiguous: false,
            isDualCollection: false,
            strategy: "error",
            bias: "auto",
            matchedSignals: [],
          },
          timings: { classificationMs: 0, searchaasMs: 0, totalMs: 0 },
          hasSummary: false,
          error: "Network error",
        },
      };
    } finally {
      setLoading(false);
    }

    setMessages((prev) => [...prev, assistantMsg]);
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="flex-shrink-0 border-b border-gray-200 bg-white px-6 py-3 flex items-center gap-3 shadow-sm">
        <div className="w-9 h-9 rounded-xl bg-blue-600 flex items-center justify-center text-white text-lg">
          🤝
        </div>
        <div>
          <h1 className="text-sm font-bold text-gray-900">Employee Support Copilot</h1>
          <p className="text-xs text-gray-500">Powered by SearchaaS · MongoDB Atlas</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          <span className="text-xs text-gray-500">Live</span>
        </div>
      </header>

      {/* Messages */}
      <main className="flex-1 overflow-y-auto px-4 py-4 max-w-3xl mx-auto w-full">
        {messages.map((msg) => (
          <ChatMessage key={msg.id} message={msg} />
        ))}
        {loading && <ThinkingIndicator />}
        <div ref={bottomRef} />
      </main>

      {/* Input */}
      <div className="flex-shrink-0 max-w-3xl mx-auto w-full">
        <ChatInput onSend={handleSend} disabled={loading} />
      </div>
    </div>
  );
}
