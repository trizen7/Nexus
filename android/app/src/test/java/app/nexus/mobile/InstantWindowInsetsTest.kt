package app.nexus.mobile

import org.junit.Assert.assertEquals
import org.junit.Test

class InstantWindowInsetsTest {
    @Test
    fun hiddenImeUsesNavigationInsetAndIgnoresStaleImeHeight() {
        assertEquals(
            InstantWindowInsets(bottomPx = 72, imeVisible = false),
            resolveInstantWindowInsets(
                imeVisible = false,
                imeBottomPx = 900,
                navigationBottomPx = 72
            )
        )
    }

    @Test
    fun visibleImeUsesItsFinalInsetImmediately() {
        assertEquals(
            InstantWindowInsets(bottomPx = 900, imeVisible = true),
            resolveInstantWindowInsets(
                imeVisible = true,
                imeBottomPx = 900,
                navigationBottomPx = 72
            )
        )
    }

    @Test
    fun visibleImeNeverShrinksBelowNavigationInset() {
        assertEquals(
            InstantWindowInsets(bottomPx = 72, imeVisible = true),
            resolveInstantWindowInsets(
                imeVisible = true,
                imeBottomPx = 0,
                navigationBottomPx = 72
            )
        )
    }
}
