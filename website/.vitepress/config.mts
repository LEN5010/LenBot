import { defineConfig } from 'vitepress'

const repo = 'https://github.com/lendevs/LenBot'

export default defineConfig({
  lang: 'zh-CN',
  title: 'LenBot',
  description: '长期待在 QQ 群里的聊天 Agent，自带网页管理面板',
  base: '/LenBot/',
  cleanUrls: true,
  lastUpdated: true,
  head: [['link', { rel: 'icon', href: '/LenBot/lenbot-favicon.svg', type: 'image/svg+xml' }]],
  themeConfig: {
    logo: '/lenbot-mark.svg',
    nav: [
      { text: '快速开始', link: '/guide/quick-start' },
      { text: '安装', link: '/guide/install-package' },
      { text: '使用', link: '/guide/panel' },
      { text: '插件开发', link: '/develop/plugins' },
      { text: '发行说明', link: `${repo}/tree/master/changelogs` },
    ],
    sidebar: {
      '/guide/': [
        { text: '开始', items: [
          { text: '快速开始', link: '/guide/quick-start' },
          { text: '接入 QQ（OneBot）', link: '/guide/onebot' },
          { text: '首次配置', link: '/guide/first-setup' },
        ] },
        { text: '安装', items: [
          { text: '部署包（Linux／macOS／Windows）', link: '/guide/install-package' },
          { text: 'Docker', link: '/guide/install-docker' },
          { text: '从源码运行', link: '/guide/install-source' },
        ] },
        { text: '日常使用', items: [
          { text: '面板一览', link: '/guide/panel' },
          { text: '角色', link: '/guide/personas' },
          { text: '插件', link: '/guide/plugins' },
          { text: '记忆', link: '/guide/memory' },
          { text: '后台任务', link: '/guide/tasks' },
          { text: '可选服务', link: '/guide/optional-services' },
        ] },
        { text: '维护', items: [
          { text: '更新与恢复', link: '/guide/update' },
          { text: '备份', link: '/guide/backup' },
          { text: '常见问题', link: '/guide/faq' },
        ] },
      ],
      '/develop/': [
        { text: '开发', items: [
          { text: '写插件', link: '/develop/plugins' },
          { text: '写角色包', link: '/develop/personas' },
          { text: '参与开发', link: '/develop/contributing' },
        ] },
      ],
    },
    socialLinks: [{ icon: 'github', link: repo }],
    editLink: { pattern: `${repo}/edit/master/website/:path`, text: '在 GitHub 上修改这一页' },
    search: {
      provider: 'local',
      options: { translations: { button: { buttonText: '搜索', buttonAriaLabel: '搜索' },
        modal: { noResultsText: '没有找到', resetButtonTitle: '清除', footer: { selectText: '选择', navigateText: '切换', closeText: '关闭' } } } },
    },
    outline: { level: [2, 3], label: '本页内容' },
    docFooter: { prev: '上一页', next: '下一页' },
    lastUpdated: { text: '最后更新' },
    returnToTopLabel: '回到顶部',
    sidebarMenuLabel: '目录',
    darkModeSwitchLabel: '外观',
    notFound: { title: '页面不存在', quote: '链接可能已经变了，试试搜索或回到首页。', linkText: '回到首页' },
    footer: { message: '程序以 AGPL-3.0-only 发布', copyright: 'LenBot' },
  },
})
