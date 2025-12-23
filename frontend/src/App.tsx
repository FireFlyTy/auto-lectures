import * as React from 'react';
import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from './api';
import './App.css';

const USER_ID = "user-123";

interface ConversationItem {
  uuid: string;
  title?: string;
  file_hash?: string;
  created_at?: string;
}

const getAudioDuration = (file: File): Promise<number> => {
  return new Promise((resolve) => {
    const audio = document.createElement('audio');
    audio.preload = 'metadata';
    audio.onloadedmetadata = () => {
      URL.revokeObjectURL(audio.src);
      resolve(audio.duration);
    };
    audio.onerror = () => resolve(0);
    audio.src = URL.createObjectURL(file);
  });
};

function App() {
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [currentConvId, setCurrentConvId] = useState<string | null>(null);
  const [currentFileHash, setCurrentFileHash] = useState<string | null>(null);
  const [messages, setMessages] = useState<any[]>([]);
  const [suggestions, setSuggestions] = useState<any[]>([]);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [stageText, setStageText] = useState("");
  const [estimatedTime, setEstimatedTime] = useState<string>("");

  // Editing state
  const [editingConvId, setEditingConvId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const editInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => { loadConversations(); }, []);

  useEffect(() => {
    if (currentConvId) {
      loadMessages(currentConvId);

      // Находим file_hash из списка conversations
      const conv = conversations.find(c => c.uuid === currentConvId);
      if (conv?.file_hash) {
        setCurrentFileHash(conv.file_hash);
        loadSuggestions(conv.file_hash);
      } else {
        setSuggestions([]);
      }

      setUploading(false);
      setProcessing(false);
      setUploadProgress(0);
      setEstimatedTime("");
    }
  }, [currentConvId, conversations]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const loadConversations = async () => {
    try {
      const data = await api.getConversations(USER_ID);
      setConversations(data.conversations || []);
    } catch (e) {
      console.error("Failed to load conversations");
      setConversations([]);
    }
  };

  const loadMessages = async (id: string) => {
    try {
      const data = await api.getMessages(id);
      const uiMsgs: any[] = [];
      (data.messages || []).forEach((m: any) => {
        if (m.prompt) uiMsgs.push({ role: 'user', text: m.prompt });
        if (m.answer) uiMsgs.push({ role: 'agent', text: m.answer });
      });
      setMessages(uiMsgs);
    } catch (e) {
      console.error(e);
      setMessages([]);
    }
  };

  const loadSuggestions = async (fileHash: string) => {
    try {
      const data = await api.getSuggestions(fileHash);
      setSuggestions(data || []);
    } catch (e) {
      console.error("Failed to load suggestions:", e);
      setSuggestions([]);
    }
  };

  const resetAllState = () => {
    setCurrentConvId(null);
    setCurrentFileHash(null);
    setConversations([]);
    setMessages([]);
    setSuggestions([]);
    setInput("");
    setUploading(false);
    setProcessing(false);
    setUploadProgress(0);
    setStageText("");
    setEstimatedTime("");
    setLoading(false);
  };

  const handleNewChat = () => {
    setCurrentConvId(null);
    setCurrentFileHash(null);
    setMessages([]);
    setSuggestions([]);
    setInput("");
    setUploading(false);
    setProcessing(false);
    setUploadProgress(0);
    setStageText("");
    setEstimatedTime("");
  };

  const handleDeleteConv = async (e: React.MouseEvent, uuid: string) => {
    e.stopPropagation();
    if (confirm("Are you sure you want to delete this conversation?")) {
      await api.deleteConversation(uuid);
      if (currentConvId === uuid) {
          handleNewChat();
      }
      loadConversations();
    }
  };

  const handleStartRename = (e: React.MouseEvent, conv: ConversationItem) => {
    e.stopPropagation();
    setEditingConvId(conv.uuid);
    setEditingTitle(conv.title || "");
    setTimeout(() => editInputRef.current?.focus(), 0);
  };

  const handleSaveRename = async () => {
    if (editingConvId && editingTitle.trim()) {
      await api.renameConversation(editingConvId, editingTitle.trim());
      setEditingConvId(null);
      setEditingTitle("");
      loadConversations();
    }
  };

  const handleCancelRename = () => {
    setEditingConvId(null);
    setEditingTitle("");
  };

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSaveRename();
    } else if (e.key === 'Escape') {
      handleCancelRename();
    }
  };

  const handleClearHistory = async () => {
    if (confirm("Warning: This will delete ALL history and files. Continue?")) {
      try {
        await api.clearHistory(USER_ID);
        resetAllState();
        await loadConversations();
      } catch (e) {
        console.error("Failed to clear history:", e);
        alert("Failed to clear history.");
      }
    }
  };

  const handleDownloadReport = () => {
    if (!messages.length) return;
    const reportHeader = `# Transcript Analysis Report\nDate: ${new Date().toLocaleString()}\n\n---\n\n`;
    const reportBody = messages.map(m => `### ${m.role.toUpperCase()}\n\n${m.text}\n`).join("\n---\n");
    const blob = new Blob([reportHeader + reportBody], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `report-${currentConvId}.md`;
    a.click();
  };

  const processFile = async (file: File) => {
    if (!file) return;

    let timeLeft = 15;
    try {
        const durationSec = await getAudioDuration(file);
        timeLeft = Math.ceil((durationSec / 80) + 20);
        setEstimatedTime(`Estimated remaining: ~${timeLeft} sec`);
    } catch (e) {
        setEstimatedTime("Calculating time...");
    }

    setUploading(true);
    setUploadProgress(0);
    setStageText("Uploading to server...");
    setProcessing(false);

    try {
      const res = await api.uploadAudio(file, USER_ID, (percent) => {
        setUploadProgress(percent);
      });

      const fileHash = res.file_hash;
      const convId = res.conversation_uuid;
      setCurrentFileHash(fileHash);

      if (res.is_cached) {
          setUploadProgress(100);
          setCurrentConvId(convId);
          await loadConversations();
          await loadSuggestions(fileHash);
          setUploading(false);
          return;
      }

      setUploadProgress(0);
      setStageText("Initializing AI...");
      setProcessing(true);

      const pollInterval = setInterval(async () => {
        if (timeLeft > 0) {
            timeLeft -= 1;
            setEstimatedTime(`Estimated remaining: ~${timeLeft} sec`);
        } else {
            setEstimatedTime("Finishing up...");
        }

        try {
            const statusRes = await api.getProcessingStatus(fileHash);

            if (statusRes.status === 'processing' || statusRes.status === 'uploading') {
                setUploadProgress(statusRes.percent || 0);
                if (statusRes.stage) setStageText(statusRes.stage);
            } else if (statusRes.status === 'completed') {
                clearInterval(pollInterval);
                setUploadProgress(100);
                setEstimatedTime("Ready!");
                setCurrentConvId(convId);
                await loadConversations();
                await loadSuggestions(fileHash);
                setUploading(false);
                setProcessing(false);
            } else if (statusRes.status === 'error') {
                clearInterval(pollInterval);
                alert("Error: " + statusRes.error);
                setUploading(false);
                setProcessing(false);
            }
        } catch (e) { console.error(e); }
      }, 1000);

    } catch (err) {
      alert("Upload failed.");
      setUploading(false);
      setProcessing(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); setIsDragging(true); };
  const handleDragLeave = (e: React.DragEvent) => { e.preventDefault(); setIsDragging(false); };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        processFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      processFile(files[0]);
    }
    e.target.value = '';
  };

  const handleSend = async (textOverride?: string) => {
    const textToSend = textOverride || input;
    if (!textToSend.trim() || !currentConvId) return;

    const userMsg = { role: 'user', text: textToSend };
    if (!textOverride) setInput("");

    setMessages(prev => [...prev, userMsg]);
    setLoading(true);

    try {
      const taskRes = await api.askQuestion(currentConvId, USER_ID, userMsg.text);
      const taskId = taskRes.task_id;
      const eventSource = new EventSource(`http://localhost:8000/transcript/task/${taskId}/stream`);

      let agentMsg = { role: 'agent', text: '' };
      setMessages(prev => [...prev, agentMsg]);

      eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'delta') {
          agentMsg.text = data.accumulated;
          setMessages(prev => {
            const newMsgs = [...prev];
            newMsgs[newMsgs.length - 1] = { ...agentMsg };
            return newMsgs;
          });
        } else if (data.type === 'done' || data.type === 'end') {
          eventSource.close();
          setLoading(false);
        } else if (data.type === 'error') {
            eventSource.close();
            setLoading(false);
            alert("Generation error");
        }
      };
    } catch (e) { setLoading(false); }
  };

  const applySuggestion = (s: any) => {
    setSuggestions(prev => prev.filter(item => item.id !== s.id));
    handleSend(s.prompt);
  };

  return (
    <div className="app-container">
      <div className="sidebar">
        <div className="sidebar-header"><span>🎙️ AutoLectures</span></div>
        <div style={{padding: '15px'}}>
            <button className="new-chat-btn" onClick={handleNewChat}>+ Upload New</button>
        </div>
        <div className="conv-list">
          {conversations.map(c => (
             <div
               key={c.uuid}
               className={`conv-item ${c.uuid === currentConvId ? 'active' : ''}`}
               onClick={() => editingConvId !== c.uuid && setCurrentConvId(c.uuid)}
             >
                {editingConvId === c.uuid ? (
                  <input
                    ref={editInputRef}
                    type="text"
                    className="rename-input"
                    value={editingTitle}
                    onChange={(e) => setEditingTitle(e.target.value)}
                    onKeyDown={handleRenameKeyDown}
                    onBlur={handleSaveRename}
                    onClick={(e) => e.stopPropagation()}
                  />
                ) : (
                  <>
                    <span
                      className="title"
                      onDoubleClick={(e) => handleStartRename(e, c)}
                      title="Double-click to rename"
                    >
                      {c.title || new Date(c.created_at || '').toLocaleDateString()}
                    </span>
                    <div className="conv-actions">
                      <span className="edit-icon" onClick={(e) => handleStartRename(e, c)}>✏️</span>
                      <span className="delete-icon" onClick={(e) => handleDeleteConv(e, c.uuid)}>×</span>
                    </div>
                  </>
                )}
             </div>
          ))}
        </div>
        <div style={{marginTop: 'auto', padding: '15px', borderTop: '1px solid #374151'}}>
            <button className="clear-history-btn" onClick={handleClearHistory}>🗑️ Clear History</button>
        </div>
      </div>

      {currentConvId ? (
        <div className="chat-area">
            <div className="chat-header">
                <span>Analysis & Chat</span>
                <button onClick={handleDownloadReport} className="download-btn">⬇ Report</button>
            </div>
            <div className="messages">
                {messages.map((m, i) => (
                    <div key={i} className={`message ${m.role}`}>
                        <div className="bubble"><ReactMarkdown>{m.text}</ReactMarkdown></div>
                    </div>
                ))}
                <div ref={messagesEndRef} />
            </div>

            {/* Suggestions перемещены вниз, над полем ввода */}
            {suggestions.length > 0 && (
                <div className="suggestions-container">
                    <div className="suggestions-scroll">
                        {suggestions.map(s => (
                            <button
                                key={s.id}
                                className="suggestion-chip"
                                onClick={() => applySuggestion(s)}
                                disabled={loading}
                            >
                                {s.label}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            <div className="input-area">
                <input
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyPress={e => e.key==='Enter' && !loading && handleSend()}
                    placeholder="Ask about the transcript..."
                    disabled={loading}
                />
                <button onClick={() => handleSend()} disabled={loading}>➤</button>
            </div>
        </div>
      ) : (
        <div className="upload-container">
          <div
            className={`drop-zone ${isDragging ? 'dragging' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => !uploading && fileInputRef.current?.click()}
          >
            <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileSelect}
                hidden
                accept="audio/*"
            />

            {!uploading ? (
              <>
                <div className="drop-icon">☁️</div>
                <div className="drop-text">Drag & Drop Audio File</div>
                <div className="drop-subtext">Supports MP3, WAV, M4A</div>
              </>
            ) : (
              <div style={{width: '100%', textAlign: 'center'}}>
                <div className="drop-icon">⏳</div>
                <div className="drop-text">{stageText}</div>
                {estimatedTime && (
                    <div style={{color: '#64748b', fontSize: '0.9rem', marginBottom: '15px'}}>
                        {estimatedTime}
                    </div>
                )}
                <div className="progress-wrapper">
                  <div className="progress-labels">
                    <span>{processing ? "Processing" : "Uploading"}</span>
                    <span>{Math.round(uploadProgress)}%</span>
                  </div>
                  <div className="progress-track">
                    <div className="progress-fill" style={{width: `${uploadProgress}%`}}/>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default App;