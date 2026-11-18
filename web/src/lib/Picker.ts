import type WhenPicker from './WhenPicker.svelte'
import type { ComponentProps } from 'svelte'

// What a page passes to <WhenPicker> when it opens one; onclose is added by the page.
export type Picker = Omit<ComponentProps<typeof WhenPicker>, 'onclose'>
