import { useStore } from '@nanostores/react'

import { GatewayProvider } from './app/gatewayContext.js'
import { useBackgroundTheme } from './app/useBackgroundTheme.js'
import { $uiState } from './app/uiStore.js'
import { useMainApp } from './app/useMainApp.js'
import { AppLayout } from './components/appLayout.js'
import type { GatewayClient } from './gatewayClient.js'

export function App({ gw }: { gw: GatewayClient }) {
  const { appActions, appComposer, appProgress, appStatus, appTranscript, gateway } = useMainApp(gw)
  const { mouseTracking } = useStore($uiState)
  // Auto-match the theme to the terminal's real background (OSC 11) so a
  // dark-profile Apple Terminal doesn't get the light palette (and vice versa).
  useBackgroundTheme()

  return (
    <GatewayProvider value={gateway}>
      <AppLayout
        actions={appActions}
        composer={appComposer}
        mouseTracking={mouseTracking}
        progress={appProgress}
        status={appStatus}
        transcript={appTranscript}
      />
    </GatewayProvider>
  )
}
