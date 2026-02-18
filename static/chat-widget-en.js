/**
 * English Chat Widget Customization
 * Translates chat widget text to English
 */

document.addEventListener('DOMContentLoaded', function() {
    // Wait for chat widget to initialize
    setTimeout(function() {
        translateChatWidget();
    }, 500);
});

function translateChatWidget() {
    // Translate chat header
    const chatHeader = document.querySelector('.chat-header h4');
    if (chatHeader && chatHeader.textContent.includes('실시간 문의')) {
        chatHeader.textContent = '💬 Live Support';
    }
    
    const chatSubtitle = document.querySelector('.chat-header p');
    if (chatSubtitle && chatSubtitle.textContent.includes('관리자에게')) {
        chatSubtitle.textContent = 'Chat with admin in real-time';
    }
    
    // Translate name form
    const nameFormTitle = document.querySelector('.chat-name-form h5');
    if (nameFormTitle && nameFormTitle.textContent.includes('이름을 입력')) {
        nameFormTitle.textContent = 'Please enter your name to start';
    }
    
    const nameInput = document.getElementById('chat-user-name');
    if (nameInput && nameInput.placeholder === '이름') {
        nameInput.placeholder = 'Name';
    }
    
    const emailInput = document.getElementById('chat-user-email');
    if (emailInput && emailInput.placeholder === '이메일 (선택)') {
        emailInput.placeholder = 'Email (optional)';
    }
    
    const startBtn = document.getElementById('chat-start-btn');
    if (startBtn && startBtn.textContent === '시작하기') {
        startBtn.textContent = 'Start';
    }
    
    // Translate message input
    const messageInput = document.getElementById('chat-message-input');
    if (messageInput && messageInput.placeholder === '메시지를 입력하세요...') {
        messageInput.placeholder = 'Type a message...';
    }
    
    // Translate closed notice
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            if (mutation.addedNodes.length) {
                mutation.addedNodes.forEach(function(node) {
                    if (node.nodeType === 1 && node.classList && node.classList.contains('chat-closed-notice')) {
                        const noticeText = node.querySelector('.closed-notice-content p');
                        if (noticeText && noticeText.textContent.includes('채팅이 종료되었습니다')) {
                            noticeText.textContent = '⚠️ Chat has ended';
                        }
                        
                        const newChatBtn = node.querySelector('.new-chat-btn');
                        if (newChatBtn && newChatBtn.textContent === '새로운 문의 시작') {
                            newChatBtn.textContent = 'Start New Chat';
                        }
                    }
                });
            }
        });
    });
    
    const messagesContainer = document.getElementById('chat-messages');
    if (messagesContainer) {
        observer.observe(messagesContainer, { childList: true, subtree: true });
    }
}
