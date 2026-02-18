/**
 * 이미지 압축 및 리사이즈 유틸리티
 * 
 * 사용법:
 * ImageCompressor.setupImageInput('photo', {
 *   maxWidth: 800,
 *   maxHeight: 800,
 *   quality: 0.85,
 *   previewElementId: 'photoPreview',
 *   previewImageId: 'previewImage'
 * });
 */

const ImageCompressor = {
    /**
     * 이미지를 압축하고 리사이즈합니다.
     * @param {File} file - 원본 이미지 파일
     * @param {number} maxWidth - 최대 너비 (기본값: 800)
     * @param {number} maxHeight - 최대 높이 (기본값: 800)
     * @param {number} quality - JPEG 품질 (0.0 ~ 1.0, 기본값: 0.85)
     * @returns {Promise<File>} 압축된 이미지 파일
     */
    compress: function(file, maxWidth = 800, maxHeight = 800, quality = 0.85) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.readAsDataURL(file);
            
            reader.onload = function(event) {
                const img = new Image();
                img.src = event.target.result;
                
                img.onload = function() {
                    // 비율을 유지하면서 리사이즈
                    let width = img.width;
                    let height = img.height;
                    
                    if (width > height) {
                        if (width > maxWidth) {
                            height = height * (maxWidth / width);
                            width = maxWidth;
                        }
                    } else {
                        if (height > maxHeight) {
                            width = width * (maxHeight / height);
                            height = maxHeight;
                        }
                    }
                    
                    // Canvas에 이미지 그리기
                    const canvas = document.createElement('canvas');
                    canvas.width = width;
                    canvas.height = height;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0, width, height);
                    
                    // Canvas를 Blob으로 변환
                    canvas.toBlob(function(blob) {
                        if (blob) {
                            // Blob을 File 객체로 변환
                            const compressedFile = new File([blob], file.name, {
                                type: 'image/jpeg',
                                lastModified: Date.now()
                            });
                            resolve(compressedFile);
                        } else {
                            reject(new Error('이미지 압축 실패'));
                        }
                    }, 'image/jpeg', quality);
                };
                
                img.onerror = function() {
                    reject(new Error('이미지 로드 실패'));
                };
            };
            
            reader.onerror = function() {
                reject(new Error('파일 읽기 실패'));
            };
        });
    },

    /**
     * 파일 input 요소에 자동 압축 기능을 설정합니다.
     * @param {string} inputId - input 요소의 ID
     * @param {Object} options - 옵션 객체
     * @param {number} options.maxWidth - 최대 너비 (기본값: 800)
     * @param {number} options.maxHeight - 최대 높이 (기본값: 800)
     * @param {number} options.quality - JPEG 품질 (기본값: 0.85)
     * @param {string} options.previewElementId - 미리보기 컨테이너 요소 ID (선택사항)
     * @param {string} options.previewImageId - 미리보기 이미지 요소 ID (선택사항)
     * @param {Function} options.onSuccess - 압축 성공 시 콜백 (선택사항)
     * @param {Function} options.onError - 에러 발생 시 콜백 (선택사항)
     */
    setupImageInput: function(inputId, options = {}) {
        const input = document.getElementById(inputId);
        if (!input) {
            console.error(`Input element with id "${inputId}" not found`);
            return;
        }

        const {
            maxWidth = 800,
            maxHeight = 800,
            quality = 0.85,
            previewElementId = null,
            previewImageId = null,
            onSuccess = null,
            onError = null
        } = options;

        input.addEventListener('change', async function(e) {
            const file = e.target.files[0];
            if (!file) return;

            // 이미지 파일인지 확인
            if (!file.type.startsWith('image/')) {
                const errorMsg = '이미지 파일만 업로드 가능합니다.';
                alert(errorMsg);
                this.value = '';
                if (onError) onError(new Error(errorMsg));
                return;
            }

            try {
                // 원본 파일 크기
                const originalSize = (file.size / 1024 / 1024).toFixed(2);
                console.log(`원본 파일 크기: ${originalSize}MB`);

                // 이미지 압축
                const compressedFile = await ImageCompressor.compress(file, maxWidth, maxHeight, quality);
                const compressedSize = (compressedFile.size / 1024 / 1024).toFixed(2);
                console.log(`압축 후 파일 크기: ${compressedSize}MB`);

                // input.files를 압축된 파일로 교체
                const dataTransfer = new DataTransfer();
                dataTransfer.items.add(compressedFile);
                this.files = dataTransfer.files;

                // 미리보기 표시
                if (previewElementId && previewImageId) {
                    const previewElement = document.getElementById(previewElementId);
                    const previewImage = document.getElementById(previewImageId);
                    
                    if (previewElement && previewImage) {
                        const reader = new FileReader();
                        reader.onload = function(e) {
                            previewImage.src = e.target.result;
                            previewElement.style.display = 'block';
                        };
                        reader.readAsDataURL(compressedFile);
                    }
                }

                // 압축 완료 메시지
                if (originalSize > compressedSize) {
                    const compressionRate = ((1 - compressedSize / originalSize) * 100).toFixed(1);
                    console.log(`${compressionRate}% 압축 완료`);
                }

                // 성공 콜백 실행
                if (onSuccess) {
                    onSuccess({
                        originalFile: file,
                        compressedFile: compressedFile,
                        originalSize: parseFloat(originalSize),
                        compressedSize: parseFloat(compressedSize)
                    });
                }

            } catch (error) {
                console.error('이미지 처리 중 오류:', error);
                alert('이미지 처리 중 오류가 발생했습니다. 다른 이미지를 선택해주세요.');
                this.value = '';
                if (onError) onError(error);
            }
        });
    }
};

// 전역으로 노출
window.ImageCompressor = ImageCompressor;
