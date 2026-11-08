<script>
  import { icons } from '../lib/icons.js';
  export let name;
  export let size = 16;
  export let label = '';
</script>

<svg class="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden={label ? undefined : 'true'} role={label ? 'img' : undefined}
  aria-label={label || undefined} style="flex:0 0 auto">
  {@html icons[name] || ''}
</svg>
