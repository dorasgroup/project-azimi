export type SourceChoice = { id: string; is_active: boolean };
export type CataloguePage<T> = { items: T[]; meta: { page: number; pages: number } };

// Empty is an intentional choice. Refreshing must never silently select all.
export function retainSourceSelection<T extends SourceChoice>(selected: string[], sources: T[]): string[] {
  const available = new Set(sources.filter(s => s.is_active).map(s => s.id));
  return selected.filter(id => available.has(id));
}

export async function loadSourceCatalogue<T>(fetchPage: (page: number) => Promise<CataloguePage<T>>): Promise<T[]> {
  const first = await fetchPage(1);
  const items = [...first.items];
  for (let page = 2; page <= first.meta.pages; page++) {
    items.push(...(await fetchPage(page)).items);
  }
  return items;
}
