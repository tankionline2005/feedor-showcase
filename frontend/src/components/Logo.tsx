import { siteName } from '../lib/site'

/** The "Feedor." wordmark (feedor-wordmark-currentcolor.svg): takes the text color, so it fits both themes. */
export function Wordmark({ width }: { width: number }) {
  return (
    <svg
      viewBox="0 0 780 140"
      width={width}
      height={(width * 140) / 780}
      role="img"
      aria-label={siteName()}
      style={{ display: 'block' }}
    >
      <path
        fill="currentColor"
        d="M80 0H180V20H160V40H80V60H120V80H80V100H60V140H0V120H20V80H60V20H80Z M460 0H500V140H420V120H400V60H420V40H460Z M180 40H240V60H260V100H200V120H260V140H180V120H160V60H180Z M220 60H200V80H220Z M300 40H360V60H380V100H320V120H380V140H300V120H280V60H300Z M340 60H320V80H340Z M460 60H440V120H460Z M540 40H600V60H620V120H600V140H540V120H520V60H540Z M580 60H560V120H580Z M640 40H720V60H680V140H640Z M720 60H740V80H720Z M760 120H780V140H760Z"
      />
    </svg>
  )
}
