export type ChatIntent = 'explain' | 'create' | 'modify' | 'debug'

export const INTENT_LABELS: Record<ChatIntent, string> = {
  explain: '问一问',
  create: '新建物件',
  modify: '改物件',
  debug: '排查问题',
}

export function detectChatIntent(message: string, hasProject: boolean): ChatIntent {
  const m = message.toLowerCase().trim()

  // Debug: error / problem signals
  if (/报错|出错|错误|失败|不工作|crash|bug|编译.*不过|不能编译|为什么.*不|怎么.*不/.test(m)) {
    return 'debug'
  }

  // Knowledge / explain: question patterns without clear modification intent
  const isQuestion =
    /[？?]$/.test(m) ||
    /^(怎么|如何|什么是?|为什么|能否|是否|有没有|how |what |why |can |is |does )/i.test(m)
  const hasModifyIntent = /修改|改成|改为|调整|换成|更改|增加|删除|添加|设置|去掉|调大|调小/.test(m)
  if (isQuestion && !hasModifyIntent) {
    return 'explain'
  }

  // Explicit create-new signals (or no project loaded yet)
  if (
    !hasProject ||
    /^(帮(我|忙)|请)?(生成|创建|新建|做)(一个|全新|新的)?(?!.*修改)/.test(m)
  ) {
    return 'create'
  }

  // Default with an open project → modify
  return 'modify'
}

export function isResumeMessage(message: string): boolean {
  return /^(继续|接着|continue|retry|重试|ok|好的?|嗯+|go on)?$/i.test(message.trim())
}
