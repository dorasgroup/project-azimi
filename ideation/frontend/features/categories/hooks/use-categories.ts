import { useQuery } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';
import { getAccessToken } from '@/lib/auth/session';
import { CategoryItem } from '../types/category.types';

export const useCategories = () => {
  return useQuery({
    queryKey: ['categories'],
    queryFn: async () => {
      const token = getAccessToken();
      if (!token) return [];
      const response = await apiRequest<{ items: CategoryItem[] }>('/categories', { token });
      return response.items || [];
    },
    staleTime: 5 * 60 * 1000,
  });
};
