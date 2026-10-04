import { useState } from 'react'
import { extractMarkdownImages } from './assistantMarkdownParser'
import { ImageLightbox } from './AssistantMarkdownView'
import type { MarkdownImageInfo } from './AssistantMarkdownView'
import { isSafeExternalUrl, openExternalUrl } from '../api/externalLinks'

/**
 * P0-B：可展开的参考图库区域（R6 闭环的展示半环）。
 *
 * 数据源 = 该条助手消息 Markdown 正文里的全部图片（搜索回复天然形成图库）。
 * 展开后并排比较：缩略图点击进应用内大图（与正文图片共用 lightbox，
 * F7：不绕过统一打开方式）；"在浏览器打开"走统一安全 opener。
 * 每张提供"采用这张作为本次参考"——采用只是显式的用户选择动作（后端
 * 原子 replace-selection），渲染图片本身不触发任何执行、不修改 HSF。
 */

export function assistantMessageImages(content: string): MarkdownImageInfo[] {
  return extractMarkdownImages(content)
}

export function AssistantReferenceGallery({
  images,
  adoptedUrl,
  onAdopt,
}: {
  images: MarkdownImageInfo[]
  adoptedUrl: string | null
  onAdopt: (url: string | null, alt: string) => void
}) {
  const [lightbox, setLightbox] = useState<MarkdownImageInfo | null>(null)
  if (!images.length) return null
  return (
    <details className="assistant-reference-gallery">
      <summary>参考图（{images.length}）· 点击展开比较 / 采用</summary>
      <div className="assistant-reference-grid">
        {images.map((image, i) => {
          const adopted = image.url === adoptedUrl
          return (
            <figure className={`assistant-reference-item${adopted ? ' is-adopted' : ''}`} key={`${image.url}-${i}`}>
              {image.safe ? (
                <button
                  type="button"
                  className="assistant-reference-thumb"
                  title="点击放大（应用内）"
                  onClick={() => setLightbox(image)}
                >
                  <img src={image.url} alt={image.alt || `参考图${i + 1}`} loading="lazy" />
                </button>
              ) : (
                <div className="assistant-ref-image-failed">不支持展示的图片地址</div>
              )}
              <figcaption>
                <span className="assistant-reference-label">{image.alt || `图${i + 1}`}</span>
                <span className="assistant-reference-actions">
                  {image.safe && isSafeExternalUrl(image.url) ? (
                    <button
                      type="button"
                      className="assistant-reference-open"
                      onClick={() => void openExternalUrl(image.url)}
                    >
                      浏览器打开
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="assistant-reference-adopt"
                    disabled={!image.safe}
                    onClick={() => onAdopt(adopted ? null : image.url, image.alt || `图${i + 1}`)}
                  >
                    {adopted ? '✓ 已采用' : '采用'}
                  </button>
                </span>
              </figcaption>
            </figure>
          )
        })}
      </div>
      {lightbox ? <ImageLightbox image={lightbox} onClose={() => setLightbox(null)} /> : null}
    </details>
  )
}
