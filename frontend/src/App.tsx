import * as React from 'react';
import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from './api';
import './App.css';

const USER_ID = "user-123";

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
  const [conversations, setConversations] = useState<any[]>([]);
  const [currentConvId, setCurrentConvId] = useState<string | null>(null);
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

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => { loadConversations(); }, []);

  useEffect(() => {
    if (currentConvId) {
      loadMessages(currentConvId);
      loadSuggestions(currentConvId);
      // Сброс состояний при входе в чат
      setUploading(false);
      setProcessing(false);
      setUploadProgress(0);
      setEstimatedTime("");
    }
  }, [currentConvId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const loadConversations = async () => {
    try {
      const data = await api.getConversations(USER_ID);
      setConversations(data.conversations);
    } catch (e) { console.error("Failed to load conversations"); }
  };

  const loadMessages = async (id: string) => {
    try {
      const data = await api.getMessages(id);
      const uiMsgs: any[] = [];
      data.messages.forEach((m: any) => {
        uiMsgs.push({ role: 'user', text: m.prompt });
        if (m.answer) uiMsgs.push({ role: 'agent', text: m.answer });
      });
      setMessages(uiMsgs);
    } catch (e) { console.error(e); }
  };

  const loadSuggestions = async (id: string) => {
    try {
      const data = await api.getSuggestions(id);
      setSuggestions(data || []);
    } catch (e) { setSuggestions([]); }
  };

  // --- ACTIONS ---

  // ИСПРАВЛЕНИЕ: Функция для полного сброса состояния при нажатии "Upload New"
  const handleNewChat = () => {
    setCurrentConvId(null);
    setMessages([]);
    setSuggestions([]);
    setInput("");

    // Сбрасываем все флаги загрузки
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
          handleNewChat(); // Используем сброс здесь тоже
      }
      loadConversations();
    }
  };

  const handleClearHistory = async () => {
    if (confirm("Warning: This will delete ALL history and files. Continue?")) {
      await api.clearHistory(USER_ID);
      setConversations([]);
      handleNewChat();
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

      setUploadProgress(0);
      setStageText("Initializing AI...");
      setProcessing(true);

      if (res.is_cached) {
          setUploadProgress(100);
          setCurrentConvId(res.conversation_uuid);
          await loadConversations();
          return;
      }

      const fileHash = res.file_hash;
      const convId = res.conversation_uuid;

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
            } else if (statusRes.status === 'error') {
                clearInterval(pollInterval);
                alert("Error: " + statusRes.error);
                setUploading(false);
            }
        } catch (e) { console.error(e); }
      }, 1000);

    } catch (err) {
      alert("Upload failed.");
      setUploading(false);
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
            {/* ИСПРАВЛЕНИЕ: Вызываем handleNewChat вместо inline функции */}
            <button className="new-chat-btn" onClick={handleNewChat}>+ Upload New</button>
        </div>
        <div className="conv-list">
          {conversations.map(c => (
             <div key={c.uuid} className={`conv-item ${c.uuid === currentConvId ? 'active' : ''}`} onClick={() => setCurrentConvId(c.uuid)}>
                <span className="title">{c.title || new Date(c.created_at).toLocaleDateString()}</span>
                <span className="delete-icon" onClick={(e) => handleDeleteConv(e, c.uuid)}>×</span>
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
                {suggestions.length > 0 && (
                     <div className="suggestions-grid">
                        {suggestions.map(s => (
                            <div key={s.id} className="suggestion-card" onClick={() => applySuggestion(s)}>
                                <div className="s-header"><span className="s-icon">✨</span><span>{s.label}</span></div>
                            </div>
                        ))}
                     </div>
                )}
                {messages.map((m, i) => (
                    <div key={i} className={`message ${m.role}`}>
                        <div className="bubble"><ReactMarkdown>{m.text}</ReactMarkdown></div>
                    </div>
                ))}
                <div ref={messagesEndRef} />
            </div>
            <div className="input-area">
                <input value={input} onChange={e => setInput(e.target.value)} onKeyPress={e => e.key==='Enter' && handleSend()} placeholder="Ask..." disabled={loading}/>
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