/**
 * 실시간 1대1 채팅 위젯
 * 우측 하단에 표시되며 클릭으로 열기/닫기 가능
 */

class ChatWidget {
    constructor() {
        this.sessionId = localStorage.getItem('chat_session_id');
        this.userName = localStorage.getItem('chat_user_name') || '방문자';
        this.isOpen = false;
        this.pollInterval = null;
        this.loggedInUser = window.__VBE_USER || null;
        this.init();
    }

    init() {
        this.createWidget();
        this.attachEventListeners();

        // 세션이 있으면 메시지 로드
        if (this.sessionId) {
            this.loadMessages();
            this.startPolling();
        }
    }

    createWidget() {
        const widget = document.createElement('div');
        widget.innerHTML = `
            <!-- 채팅 버튼 -->
            <button id="chat-toggle-btn" class="chat-toggle-btn" title="문의하기">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#333" stroke-width="2">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                </svg>
                <span id="chat-unread-badge" class="chat-unread-badge" style="display:none;">0</span>
            </button>

            <!-- 채팅 창 -->
            <div id="chat-window" class="chat-window" style="display:none;">
                <div class="chat-header">
                    <div>
                        <h4>💬 실시간 문의</h4>
                        <p>관리자에게 실시간으로 문의하세요</p>
                    </div>
                    <div class="chat-header-actions">
                        <button id="chat-end-btn" class="chat-end-btn">종료</button>
                        <button id="chat-close-btn" class="chat-close-btn">&times;</button>
                    </div>
                </div>

                <!-- 이름 입력 폼 -->
                <div id="chat-name-form" class="chat-name-form">
                    <h5>문의를 시작하려면 이름을 입력해주세요</h5>
                    <input type="text" id="chat-user-name" placeholder="이름" value="${this.loggedInUser && this.loggedInUser.loggedIn ? this.loggedInUser.name : this.userName}">
                    <input type="email" id="chat-user-email" placeholder="이메일 (선택)" value="${this.loggedInUser && this.loggedInUser.loggedIn && this.loggedInUser.email ? this.loggedInUser.email : ''}">
                    <button id="chat-start-btn" class="chat-start-btn">시작하기</button>
                </div>

                <!-- 채팅 메시지 영역 -->
                <div id="chat-messages" class="chat-messages" style="display:none;">
                    <!-- 메시지들이 여기에 표시됩니다 -->
                </div>

                <!-- 메시지 입력 -->
                <div id="chat-input-area" class="chat-input-area" style="display:none;">
                    <input type="text" id="chat-message-input" placeholder="메시지를 입력하세요..." maxlength="500">
                    <button id="chat-send-btn" class="chat-send-btn">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="22" y1="2" x2="11" y2="13"></line>
                            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                        </svg>
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(widget);
    }

    attachEventListeners() {
        // 토글 버튼
        document.getElementById('chat-toggle-btn').addEventListener('click', () => {
            this.toggleChat();
        });

        // 닫기 버튼 (창만 닫기)
        document.getElementById('chat-close-btn').addEventListener('click', () => {
            this.closeChat();
        });

        // 채팅 종료 버튼 (세션 종료)
        document.getElementById('chat-end-btn').addEventListener('click', () => {
            this.endChat();
        });

        // 채팅 시작
        document.getElementById('chat-start-btn').addEventListener('click', () => {
            this.startChat();
        });

        // 메시지 전송
        document.getElementById('chat-send-btn').addEventListener('click', () => {
            this.sendMessage();
        });

        // Enter 키로 전송
        document.getElementById('chat-message-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
    }

    getLanguage() {
        // 언어 감지 (영어 버전 확인)
        return document.documentElement.lang === 'en' ||
               window.location.search.includes('lang=en') ||
               document.querySelector('html[lang="en"]') ? 'en' : 'ko';
    }

    toggleChat() {
        const chatWindow = document.getElementById('chat-window');
        this.isOpen = !this.isOpen;
        chatWindow.style.display = this.isOpen ? 'flex' : 'none';

        if (this.isOpen) {
            // 읽음 배지 숨기기
            document.getElementById('chat-unread-badge').style.display = 'none';

            // 세션이 있으면 메시지 로드
            if (this.sessionId) {
                this.showChatArea();
                this.loadMessages();
                if (!this.pollInterval) {
                    this.startPolling();
                }
            } else if (this.loggedInUser && this.loggedInUser.loggedIn) {
                // 로그인 유저는 이름 입력 건너뛰고 자동 시작
                this.autoStartChat();
            }
        } else {
            if (this.pollInterval) {
                clearInterval(this.pollInterval);
                this.pollInterval = null;
            }
        }
    }

    closeChat() {
        this.isOpen = false;
        document.getElementById('chat-window').style.display = 'none';
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
    }

    async startChat() {
        const nameInput = document.getElementById('chat-user-name');
        const emailInput = document.getElementById('chat-user-email');
        const name = nameInput.value.trim();
        const email = emailInput.value.trim();
        const lang = this.getLanguage();

        if (!name) {
            alert(lang === 'en' ? 'Please enter your name.' : '이름을 입력해주세요.');
            return;
        }

        try {
            const response = await fetch('/chat/start', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ name, email })
            });

            const data = await response.json();

            if (data.success) {
                this.sessionId = data.session_id;
                this.userName = name;
                localStorage.setItem('chat_session_id', this.sessionId);
                localStorage.setItem('chat_user_name', name);

                this.showChatArea();
                this.startPolling();
            }
        } catch (error) {
            console.error('채팅 시작 오류:', error);
            alert(lang === 'en' ? 'Unable to start chat. Please try again.' : '채팅을 시작할 수 없습니다. 다시 시도해주세요.');
        }
    }

    async autoStartChat() {
        const name = this.loggedInUser.name || '방문자';
        const email = this.loggedInUser.email || '';

        // 즉시 이름 폼 숨기고 채팅 영역 표시 (로딩 상태)
        this.showChatArea();

        try {
            const response = await fetch('/chat/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, email })
            });

            const data = await response.json();

            if (data.success) {
                this.sessionId = data.session_id;
                this.userName = name;
                localStorage.setItem('chat_session_id', this.sessionId);
                localStorage.setItem('chat_user_name', name);
                this.startPolling();
            } else {
                // 서버 에러 시 이름 폼으로 복귀
                this.showNameForm();
            }
        } catch (error) {
            console.error('Auto chat start error:', error);
            // 네트워크 에러 시 이름 폼으로 복귀
            this.showNameForm();
        }
    }

    showNameForm() {
        const nameForm = document.getElementById('chat-name-form');
        const messagesArea = document.getElementById('chat-messages');
        const inputArea = document.getElementById('chat-input-area');

        if (nameForm) nameForm.style.display = 'flex';
        if (messagesArea) messagesArea.style.display = 'none';
        if (inputArea) inputArea.style.display = 'none';

        // 로그인 유저 정보 미리 채우기
        if (this.loggedInUser && this.loggedInUser.loggedIn) {
            const nameInput = document.getElementById('chat-user-name');
            const emailInput = document.getElementById('chat-user-email');
            if (nameInput && this.loggedInUser.name) nameInput.value = this.loggedInUser.name;
            if (emailInput && this.loggedInUser.email) emailInput.value = this.loggedInUser.email;
        }
    }

    async endChat() {
        const lang = this.getLanguage();

        if (!this.sessionId) {
            this.closeChat();
            return;
        }

        const confirmMsg = lang === 'en'
            ? 'Do you want to end this chat? You can start a new chat later.'
            : '이 대화를 종료하시겠습니까? 이후에도 새로 문의를 시작할 수 있습니다.';

        if (!confirm(confirmMsg)) return;

        try {
            const response = await fetch('/chat/close', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ session_id: this.sessionId })
            });

            const data = await response.json();
            if (data.success) {
                this.handleSessionClosed();
            } else {
                alert(data.error || (lang === 'en' ? 'Failed to end chat.' : '채팅 종료에 실패했습니다.'));
            }
        } catch (error) {
            console.error('채팅 종료 오류:', error);
            alert(lang === 'en' ? 'Failed to end chat.' : '채팅 종료 중 오류가 발생했습니다.');
        }
    }

        showChatArea() {
        document.getElementById('chat-name-form').style.display = 'none';
        document.getElementById('chat-messages').style.display = 'block';
        document.getElementById('chat-input-area').style.display = 'flex';
    }

    async sendMessage() {
        const input = document.getElementById('chat-message-input');
        const message = input.value.trim();

        if (!message) return;

        try {
            const response = await fetch('/chat/send', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    session_id: this.sessionId,
                    message: message,
                    sender_type: 'user',
                    sender_name: this.userName
                })
            });

            const data = await response.json();

            if (data.success) {
                input.value = '';
                this.loadMessages();
            }
        } catch (error) {
            console.error('메시지 전송 오류:', error);
        }
    }

    async loadMessages() {
        if (!this.sessionId) return;

        try {
            const response = await fetch(`/api/chat/messages/${this.sessionId}`);
            const data = await response.json();

            if (data.success) {
                this.displayMessages(data.messages);

                // 세션 종료 확인
                if (data.session_status === 'closed') {
                    this.handleSessionClosed();
                }
            }
        } catch (error) {
            console.error('메시지 로드 오류:', error);
        }
    }

    handleSessionClosed() {
        // 폴링 중지
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }

        // 세션 정보 삭제
        localStorage.removeItem('chat_session_id');
        localStorage.removeItem('chat_user_name');
        this.sessionId = null;

        // 입력창 숨기고 종료 메시지 표시
        const inputArea = document.getElementById('chat-input-area');
        if (inputArea) {
            inputArea.style.display = 'none';
        }

        // 종료 메시지 추가
        const lang = this.getLanguage();
        const messagesContainer = document.getElementById('chat-messages');
        if (messagesContainer && !document.getElementById('chat-closed-notice')) {
            const closedNotice = document.createElement('div');
            closedNotice.id = 'chat-closed-notice';
            closedNotice.className = 'chat-closed-notice';
            closedNotice.innerHTML = `
                <div class="closed-notice-content">
                    <p>${lang === 'en' ? '⚠️ Chat has been closed' : '⚠️ 채팅이 종료되었습니다'}</p>
                    <button onclick="chatWidget.startNewChat()" class="new-chat-btn">${lang === 'en' ? 'Start new inquiry' : '새로운 문의 시작'}</button>
                </div>
            `;
            messagesContainer.appendChild(closedNotice);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
    }

    startNewChat() {
        // 기존 세션 정보 삭제
        localStorage.removeItem('chat_session_id');
        localStorage.removeItem('chat_user_name');

        // 폴링 중지
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }

        // 상태 초기화
        this.sessionId = null;
        this.userName = '방문자';

        // 메시지 영역 초기화
        const messagesArea = document.getElementById('chat-messages');
        if (messagesArea) {
            messagesArea.innerHTML = '';
        }

        // 로그인 유저는 자동으로 새 채팅 시작 (autoStartChat이 showChatArea를 즉시 호출)
        if (this.loggedInUser && this.loggedInUser.loggedIn) {
            this.autoStartChat();
            return;
        }

        // 비로그인 유저는 이름 입력 폼 표시
        this.showNameForm();
    }

    displayMessages(messages) {
        const messagesContainer = document.getElementById('chat-messages');
        const shouldScroll = messagesContainer.scrollTop + messagesContainer.clientHeight >= messagesContainer.scrollHeight - 50;

        const lang = this.getLanguage();
        const isEnglish = lang === 'en';

        messagesContainer.innerHTML = messages.map(msg => {
            const isUser = msg.sender_type === 'user';
            const locale = isEnglish ? 'en-US' : 'ko-KR';
            const adminLabel = isEnglish ? 'Admin' : '관리자';

            const time = new Date(msg.created_at).toLocaleTimeString(locale, {
                hour: '2-digit',
                minute: '2-digit',
                hour12: true
            });

            return `
                <div class="chat-message ${isUser ? 'user' : 'admin'}">
                    ${!isUser ? `<div class="sender-name">${msg.sender_name || adminLabel}</div>` : ''}
                    <div class="message-content">
                        ${this.escapeHtml(msg.message)}
                    </div>
                    <div class="message-time">${time}</div>
                </div>
            `;
        }).join('');

        if (shouldScroll) {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }

        // 읽지 않은 관리자 메시지 배지
        if (!this.isOpen) {
            const unreadCount = messages.filter(m => m.sender_type === 'admin').length;
            const badge = document.getElementById('chat-unread-badge');
            if (unreadCount > 0) {
                badge.textContent = unreadCount;
                badge.style.display = 'flex';
            } else {
                badge.style.display = 'none';
            }
        }
    }

    startPolling() {
        this.pollInterval = setInterval(() => {
            this.loadMessages();
        }, 3000); // 3초마다 새 메시지 확인
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// 전역 변수로 채팅 위젯 인스턴스 저장
let chatWidget;

// 페이지 로드 시 채팅 위젯 초기화
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        chatWidget = new ChatWidget();
    });
} else {
    chatWidget = new ChatWidget();
}
