import { extractMarkdownImages } from './assistantMarkdownParser'
import type { MarkdownImageInfo } from './AssistantMarkdownView'

/**
 * P0-B：可展开的参考图库区域（R6 闭环的展示半环）。
 *
 * 数据源 = 该条助手消息 Markdown 正文里的全部图片（搜索回复天然形成图库）。
 * 展开后并排比较，每张提供"采用这张作为本次参考"——采用只是显式的用户
 * 选择动作（P1-A 起写入会话引用资产并进入下一轮执行契约），渲染图片本身
 * 不触发任何执行、不修改 HSF。
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
                <a
                  className="assistant-reference-thumb"
                  href={image.url}
                  title="点击放大 / 打开来源"
                  onClick={(event) => {
                    event.preventDefault()
                    window.open(image.url, '_blank', 'noopener,noreferrer')
                  }}
                >
                  <img src={image.url} alt={image.alt || `参考图${i + 1}`} loading="lazy" />
                </a>
              ) : (
                <div className="assistant-ref-image-failed">不支持展示的图片地址</div>
              )}
              <figcaption>
                <span className="assistant-reference-label">{image.alt || `图${i + 1}`}</span>
                <button
                  type="button"
                  className="assistant-reference-adopt"
                  disabled={!image.safe}
                  onClick={() => onAdopt(adopted ? null : image.url, image.alt || `图${i + 1}`)}
                >
                  {adopted ? '✓ 已采用为本次参考' : '采用这张作为本次参考'}
                </button>
              </figcaption>
            </figure>
          )
        })}
      </div>
    </details>
  )
}
