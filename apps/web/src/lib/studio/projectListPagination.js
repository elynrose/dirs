/** Recent projects sidebar: items per page. */
export const PROJECTS_LIST_PAGE_SIZE = 6;

export function paginateProjectList(projects, page) {
  const list = Array.isArray(projects) ? projects : [];
  const pageCount = Math.max(1, Math.ceil(list.length / PROJECTS_LIST_PAGE_SIZE));
  const safePage = Math.min(Math.max(0, Number(page) || 0), pageCount - 1);
  const start = safePage * PROJECTS_LIST_PAGE_SIZE;
  return {
    visible: list.slice(start, start + PROJECTS_LIST_PAGE_SIZE),
    page: safePage,
    pageCount,
    canPrev: safePage > 0,
    canNext: safePage < pageCount - 1,
    showPager: list.length > PROJECTS_LIST_PAGE_SIZE,
    total: list.length,
  };
}
