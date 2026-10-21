/**
 * 下载工具：用 fetch + Blob 触发保存。
 *
 * 为什么不用 <a href download>：本地是自签证书的 HTTPS，Chrome/Safari 会拦截
 * 来自"证书有警告"页面的导航式下载（提示"已阻止不安全的下载"）。
 * 走 Blob 后属于同源脚本内生成的对象 URL，不会被拦。
 */
export async function downloadFile(url: string, filename: string): Promise<void> {
  let blob: Blob;
  try {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) {
      let detail = '';
      try {
        const data = await response.json();
        detail = data?.detail?.message || data?.message || '';
      } catch {
        /* 非 JSON 响应 */
      }
      throw new Error(detail || `下载失败（HTTP ${response.status}）`);
    }
    blob = await response.blob();
  } catch (error) {
    // 兜底：直接打开新窗口（用户可另存为）
    window.open(url, '_blank');
    throw error;
  }
  saveBlob(blob, filename);
}

/** 把内存里的 Blob 触发一次「另存为」（与 downloadFile 共用同一套规避拦截的做法） */
export function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename || 'download';
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 5000);
}

/** 从 URL 里猜一个文件名 */
export function filenameFromUrl(url: string, fallback = 'download'): string {
  try {
    const clean = url.split('?')[0];
    const last = clean.substring(clean.lastIndexOf('/') + 1);
    return decodeURIComponent(last) || fallback;
  } catch {
    return fallback;
  }
}
