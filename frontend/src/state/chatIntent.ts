export type ChatIntent = 'explain' | 'create' | 'modify' | 'debug'

export const INTENT_LABELS: Record<ChatIntent, string> = {
  explain: '问一问',
  create: '新建物件',
  modify: '改物件',
  debug: '排查问题',
}

export function detectChatIntent(message: string, hasProject: boolean): ChatIntent {
  // 剥离多图 token（[图1] [图2]…）再判定：token 前缀会让
  // "生成/创建/新建" 等行首信号失效，把"生成新构件"误判成"改当前项目"
  // （建筑基础_v1 被坐斗全文重写事故的根因之一）
  const m = message.toLowerCase().trim().replace(/^(\s*\[图\d+\])+/, '').trim()

  // Debug: error / problem signals
  if (/报错|出错|错误|失败|不工作|crash|bug|编译.*不过|不能编译|为什么.*不|怎么.*不/.test(m)) {
    return 'debug'
  }

  const hasModifyIntent = /修改|改成|改为|调整|换成|更改|增加|删除|添加|设置|去掉|调大|调小/.test(m)

  // Knowledge / explain: question patterns without clear modification intent
  const isQuestion =
    /[？?]$/.test(m) ||
    /^(怎么|如何|什么是?|为什么|能否|是否|有没有|how |what |why |can |is |does )/i.test(m)
  if (isQuestion && !hasModifyIntent) {
    return 'explain'
  }

  // Explicit create-new signals.
  // 生成类动词出现在句中也算 create（"参考图1生成坐斗"），但明确修改词优先
  if (
    !hasModifyIntent &&
    (/^(帮(我|忙)|请)?(生成|创建|新建|做)(一个|全新|新的)?/.test(m) ||
      /(生成|创建|新建|重做)/.test(m))
  ) {
    return 'create'
  }

  // 无项目时不落盘：没有明确生成意图的闲聊/陈述只答不动，
  // 等有产出物意图（create）才自动建项目目录
  if (!hasProject) {
    return 'explain'
  }

  // Default with an open project → modify
  return 'modify'
}

export function isResumeMessage(message: string): boolean {
  return /^(继续|接着|continue|retry|重试|ok|好的?|嗯+|go on)?$/i.test(message.trim())
}
