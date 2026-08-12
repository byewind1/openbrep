import { describe, it, expect } from 'vitest'
import { detectChatIntent, isResumeMessage } from './chatIntent'

describe('detectChatIntent', () => {
  it('[图N] token 前缀不挡住 create 判定（建筑基础_v1 事故回归）', () => {
    expect(detectChatIntent('[图1]参考图1生成中国古建筑斗拱中坐斗gdl构件', true)).toBe('create')
    expect(detectChatIntent('[图1][图2]生成一个书架', true)).toBe('create')
    expect(detectChatIntent('[图3] 做一个斗', true)).toBe('create')
  })

  it('生成/创建/新建开头 → create（有项目打开也一样）', () => {
    expect(detectChatIntent('生成一个书架', true)).toBe('create')
    expect(detectChatIntent('新建一个窗', true)).toBe('create')
    expect(detectChatIntent('帮我做一个斗拱', true)).toBe('create')
  })

  it('无项目时一律 create', () => {
    expect(detectChatIntent('把层板改成5', false)).toBe('create')
  })

  it('修改类表述 + 有项目 → modify', () => {
    expect(detectChatIntent('把层板数改成5', true)).toBe('modify')
    expect(detectChatIntent('[图1]把纹样改成冰裂', true)).toBe('modify')
    expect(detectChatIntent('增加一个背板', true)).toBe('modify')
  })

  it('问题 → explain；报错 → debug', () => {
    expect(detectChatIntent('这段代码是什么意思？', true)).toBe('explain')
    expect(detectChatIntent('编译报错了', true)).toBe('debug')
  })
})

describe('isResumeMessage', () => {
  it('继续/空消息识别', () => {
    expect(isResumeMessage('继续')).toBe(true)
    expect(isResumeMessage('')).toBe(true)
    expect(isResumeMessage('生成一个书架')).toBe(false)
  })
})
