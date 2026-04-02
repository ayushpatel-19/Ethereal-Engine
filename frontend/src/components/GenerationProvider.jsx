import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../lib/api";
import { useToast } from "./ToastProvider";

const SESSION_STORAGE_KEY = "ethereal_generation_session_v1";

const DEFAULT_SESSION_STATS = {
  queries: 0,
  totalLatency: 0,
  avgLatency: "-"
};

const DEFAULT_SYSTEM_STATS = {
  cacheHitRate: "-",
  ollamaStatus: "-",
  totalQueries: 0
};

const DEFAULT_STATE = {
  query: "",
  messages: [],
  chatHistory: [],
  models: [],
  modelProviders: {},
  selectedModel: "",
  isGenerating: false,
  latestContext: [],
  latestCitations: [],
  confidence: null,
  sessionStats: DEFAULT_SESSION_STATS,
  systemStats: DEFAULT_SYSTEM_STATS
};

const GenerationSessionContext = createContext(null);

function makeId() {
  return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function safeParse(value, fallback) {
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function normalizePersistedMessages(messages) {
  return (messages || []).map((message) => {
    if (!message?.isLoading && !message?.isStreaming) {
      return message;
    }

    return {
      ...message,
      isLoading: false,
      isStreaming: false,
      content: message.content || "Generation was interrupted. Please retry."
    };
  });
}

function loadInitialState() {
  if (typeof window === "undefined") {
    return DEFAULT_STATE;
  }

  const persisted = safeParse(sessionStorage.getItem(SESSION_STORAGE_KEY), null);
  if (!persisted) {
    return DEFAULT_STATE;
  }

  return {
    ...DEFAULT_STATE,
    ...persisted,
    isGenerating: false,
    messages: normalizePersistedMessages(persisted.messages),
    sessionStats: {
      ...DEFAULT_SESSION_STATS,
      ...(persisted.sessionStats || {})
    },
    systemStats: {
      ...DEFAULT_SYSTEM_STATS,
      ...(persisted.systemStats || {})
    }
  };
}

export function GenerationProvider({ children }) {
  const toast = useToast();
  const initialStateRef = useRef(null);
  const bootLoadedRef = useRef(false);

  if (initialStateRef.current === null) {
    initialStateRef.current = loadInitialState();
  }

  const initialState = initialStateRef.current;

  const [query, setQuery] = useState(initialState.query);
  const [messages, setMessages] = useState(initialState.messages);
  const [chatHistory, setChatHistory] = useState(initialState.chatHistory);
  const [models, setModels] = useState(initialState.models);
  const [modelProviders, setModelProviders] = useState(initialState.modelProviders);
  const [selectedModel, setSelectedModel] = useState(initialState.selectedModel);
  const [isGenerating, setIsGenerating] = useState(false);
  const [latestContext, setLatestContext] = useState(initialState.latestContext);
  const [latestCitations, setLatestCitations] = useState(initialState.latestCitations);
  const [confidence, setConfidence] = useState(initialState.confidence);
  const [sessionStats, setSessionStats] = useState(initialState.sessionStats);
  const [systemStats, setSystemStats] = useState(initialState.systemStats);

  const queryRef = useRef(query);
  const chatHistoryRef = useRef(chatHistory);
  const selectedModelRef = useRef(selectedModel);
  const isGeneratingRef = useRef(isGenerating);

  useEffect(() => {
    queryRef.current = query;
  }, [query]);

  useEffect(() => {
    chatHistoryRef.current = chatHistory;
  }, [chatHistory]);

  useEffect(() => {
    selectedModelRef.current = selectedModel;
  }, [selectedModel]);

  useEffect(() => {
    isGeneratingRef.current = isGenerating;
  }, [isGenerating]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    sessionStorage.setItem(
      SESSION_STORAGE_KEY,
      JSON.stringify({
        query,
        messages,
        chatHistory,
        models,
        modelProviders,
        selectedModel,
        isGenerating,
        latestContext,
        latestCitations,
        confidence,
        sessionStats,
        systemStats
      })
    );
  }, [
    chatHistory,
    confidence,
    isGenerating,
    latestCitations,
    latestContext,
    messages,
    modelProviders,
    models,
    query,
    selectedModel,
    sessionStats,
    systemStats
  ]);

  useEffect(() => {
    if (bootLoadedRef.current) {
      return;
    }

    bootLoadedRef.current = true;

    const loadBootData = async () => {
      try {
        const [modelData, statsData] = await Promise.all([
          api.system.models(),
          api.system.stats()
        ]);
        const availableModels = modelData.chat_models || modelData.models || [];
        const defaultModel = modelData.default_chat_model || availableModels[0] || "";

        setModels(availableModels);
        setModelProviders(modelData.providers || {});
        setSelectedModel((current) => (
          current && availableModels.includes(current) ? current : defaultModel
        ));

        if (modelData.errors?.groq) {
          toast(`Groq models unavailable: ${modelData.errors.groq}`, "warning");
        }

        setSystemStats({
          cacheHitRate: `${Math.round((statsData.cache_hit_rate || 0) * 100)}%`,
          ollamaStatus: statsData.ollama_status || "-",
          totalQueries: statsData.total_queries || 0
        });
      } catch (error) {
        toast(error.message, "warning");
      }
    };

    void loadBootData();
  }, [toast]);

  async function submitFeedback(messageId, rating) {
    const target = messages.find((message) => message.id === messageId);
    if (!target || target.role !== "assistant") {
      return;
    }

    try {
      await api.feedback.submit(
        target.query || queryRef.current,
        target.content,
        rating,
        "",
        target.chunkIds || []
      );
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId ? { ...message, feedback: rating } : message
        )
      );
      toast(rating === "up" ? "Feedback recorded" : "Feedback recorded for improvement", "success");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function clearChat() {
    setQuery("");
    setMessages([]);
    setChatHistory([]);
    setLatestContext([]);
    setLatestCitations([]);
    setConfidence(null);
    setSessionStats(DEFAULT_SESSION_STATS);
  }

  async function sendMessage(overrideQuery = null, prefetched = null) {
    if (isGeneratingRef.current) {
      return;
    }

    const activeQuery = (overrideQuery ?? queryRef.current).trim();
    if (!activeQuery) {
      toast("Enter a question first", "warning");
      return;
    }

    setIsGenerating(true);
    setQuery("");

    const assistantId = makeId();
    const nextHistory = [...chatHistoryRef.current, { role: "user", content: activeQuery }];

    setMessages((current) => [
      ...current,
      { id: makeId(), role: "user", content: activeQuery },
      { id: assistantId, role: "assistant", content: "", isLoading: true, isStreaming: false }
    ]);

    try {
      let retrievalData = prefetched;
      if (!retrievalData || !retrievalData.results?.length) {
        retrievalData = await api.retrieval.query({
          query: activeQuery,
          topK: 5,
          useReranking: true
        });
      }

      const contextResults = retrievalData?.results || [];
      const contextChunks = retrievalData?.contextChunks || retrievalData?.context_chunks || [];
      setLatestContext(contextResults);

      let streamedAnswer = "";
      const response = await api.generation.stream({
        query: activeQuery,
        contextChunks,
        chatHistory: nextHistory,
        model: selectedModelRef.current || null,
        onToken: (token) => {
          streamedAnswer += token;
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content: streamedAnswer,
                    isLoading: false,
                    isStreaming: true
                  }
                : message
            )
          );
        }
      });

      const finalAnswer = response?.answer || streamedAnswer;
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: finalAnswer,
                isLoading: false,
                isStreaming: false,
                latency: response?.latency_ms || null,
                model: response?.model || selectedModelRef.current || null,
                confidence: response?.confidence ?? null,
                feedback: null,
                chunkIds: (response?.citations || []).map((citation) => citation.chunk_id),
                query: activeQuery
              }
            : message
        )
      );

      setLatestCitations(response?.citations || []);
      setConfidence(response?.confidence ?? null);
      setChatHistory([...nextHistory, { role: "assistant", content: finalAnswer }]);
      setSessionStats((current) => {
        const queries = current.queries + 1;
        const totalLatency = current.totalLatency + (response?.latency_ms || 0);
        return {
          queries,
          totalLatency,
          avgLatency: `${Math.round(totalLatency / queries)}ms`
        };
      });

      if (!contextResults.length) {
        toast("No chunks matched strongly. The answer may be limited.", "warning");
      }
    } catch (error) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: `Error: ${error.message}. Make sure Ollama is running for retrieval, your selected generation provider is configured, and documents are ingested.`,
                isLoading: false,
                isStreaming: false
              }
            : message
        )
      );
      toast(error.message, "error");
    } finally {
      setIsGenerating(false);
    }
  }

  const value = useMemo(
    () => ({
      query,
      setQuery,
      messages,
      chatHistory,
      models,
      modelProviders,
      selectedModel,
      setSelectedModel,
      isGenerating,
      latestContext,
      setLatestContext,
      latestCitations,
      confidence,
      sessionStats,
      systemStats,
      clearChat,
      sendMessage,
      submitFeedback
    }),
    [
      chatHistory,
      confidence,
      isGenerating,
      latestCitations,
      latestContext,
      messages,
      modelProviders,
      models,
      query,
      selectedModel,
      sessionStats,
      systemStats
    ]
  );

  return (
    <GenerationSessionContext.Provider value={value}>
      {children}
    </GenerationSessionContext.Provider>
  );
}

export function useGenerationSession() {
  const context = useContext(GenerationSessionContext);
  if (!context) {
    throw new Error("useGenerationSession must be used within a GenerationProvider");
  }
  return context;
}
