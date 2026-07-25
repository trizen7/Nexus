package app.nexus.mobile

import androidx.compose.runtime.Immutable

@Immutable
internal data class InstantWindowInsets(
    val bottomPx: Int = 0,
    val imeVisible: Boolean = false
)

internal fun resolveInstantWindowInsets(
    imeVisible: Boolean,
    imeBottomPx: Int,
    navigationBottomPx: Int
): InstantWindowInsets = InstantWindowInsets(
    bottomPx = if (imeVisible) maxOf(imeBottomPx, navigationBottomPx) else navigationBottomPx,
    imeVisible = imeVisible
)
