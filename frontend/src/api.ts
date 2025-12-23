const BASE_URL = 'http://localhost:8000';

export const api = {
  uploadAudio: (
    file: File,
    userUuid: string,
    onProgress: (percent: number) => void
  ): Promise<any> => {
    return new Promise((resolve, reject) => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('user_uuid', userUuid);

      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${BASE_URL}/conversations/upload`);

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress((event.loaded / event.total) * 100);
        }
      };

      xhr.onload = () => {
        if (xhr.status === 200) {
          resolve(JSON.parse(xhr.response));
        } else {
          reject(new Error('Upload failed'));
        }
      };

      xhr.onerror = () => reject(new Error('Network error'));
      xhr.send(formData);
    });
  },

  getProcessingStatus: async (fileHash: string) => {
    const res = await fetch(`${BASE_URL}/conversations/processing/${fileHash}`);
    return res.json();
  },

  getConversations: async (userUuid: string) => {
    const res = await fetch(`${BASE_URL}/conversations/list?user_uuid=${userUuid}`);
    return res.json();
  },

  getMessages: async (convId: string) => {
    const res = await fetch(`${BASE_URL}/conversations/${convId}/messages`);
    return res.json();
  },

  getSuggestions: async (fileHash: string) => {
    const res = await fetch(`${BASE_URL}/suggestions/${fileHash}`);
    return res.json();
  },

  deleteConversation: async (convId: string) => {
    const res = await fetch(`${BASE_URL}/conversations/${convId}`, { method: 'DELETE' });
    return res.json();
  },

  renameConversation: async (convId: string, title: string) => {
    const res = await fetch(`${BASE_URL}/conversations/${convId}/rename?title=${encodeURIComponent(title)}`, {
      method: 'PATCH'
    });
    return res.json();
  },

  clearHistory: async (userUuid: string) => {
    const res = await fetch(`${BASE_URL}/conversations/clear?user_uuid=${userUuid}`, { method: 'DELETE' });
    return res.json();
  },

  askQuestion: async (convId: string, userUuid: string, prompt: string) => {
    const res = await fetch(`${BASE_URL}/transcript/task`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation: { uuid: convId, user_uuid: userUuid },
        message: {
            uuid: crypto.randomUUID(),
            user_uuid: userUuid,
            conversation_uuid: convId,
            prompt: prompt
        },
        stream: true
      }),
    });
    return res.json();
  }
};