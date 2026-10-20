import { cn } from '@/lib/utils';
import { Loader2Icon } from 'lucide-react';

/* oxlint-disable jsx-a11y/prefer-tag-over-role -- The loading graphic retains its SVG API and explicit status role; output is a different element with different props. */
function Spinner({ className, ...props }: React.ComponentProps<'svg'>) {
  return (
    <Loader2Icon
      data-slot="spinner"
      role="status"
      aria-label="Loading"
      className={cn('size-4 animate-spin', className)}
      {...props}
    />
  );
}
/* oxlint-enable jsx-a11y/prefer-tag-over-role */

export { Spinner };
