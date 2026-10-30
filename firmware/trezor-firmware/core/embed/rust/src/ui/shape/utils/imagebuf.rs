use crate::ui::geometry::Offset;
use crate::ui::shape::{Bitmap, BitmapView, Canvas, CanvasBuilder};
use crate::ui::UIError;

use crate::trezorhal::display::{DISPLAY_RESX, DISPLAY_RESY};

/// Size of image buffer in bytes: a whole screen of 16-bit RGB565 pixels, and
/// at least 240x240. The home and lock screens draw their background into it
/// at full screen size, so a 240x320 display (T3T2) needs more than the
/// 240x240 the buffer used to be fixed at; with less, both screens fail to be
/// created and never appear.
const SCREEN_IMAGE_BYTES: usize = DISPLAY_RESX as usize * DISPLAY_RESY as usize * 2;
const IMAGE_BUFFER_SIZE: usize = if SCREEN_IMAGE_BYTES > 240 * 240 * 2 {
    SCREEN_IMAGE_BYTES
} else {
    240 * 240 * 2
};

#[repr(align(16))]
struct AlignedBuffer {
    bytes: [u8; IMAGE_BUFFER_SIZE],
}

/// Raw image buffer used by `ImageBuffer` instances.
static mut IMAGE_BUFFER: AlignedBuffer = AlignedBuffer {
    bytes: [0; IMAGE_BUFFER_SIZE],
};

/// Set to `true` if the `IMAGE_BUFFER` is locked
/// (in use by some instance of `ImageBuffer`)
static mut IMAGE_BUFFER_LOCKED: bool = false;

/// A struct representing an image buffer - generic buffer for mutable images.
pub struct ImageBuffer<T: Canvas + CanvasBuilder<'static>> {
    canvas: T,
}

impl<T> Drop for ImageBuffer<T>
where
    T: Canvas + CanvasBuilder<'static>,
{
    fn drop(&mut self) {
        // It's safe to modify static variable as whole app
        // is single-threaded.
        unsafe {
            IMAGE_BUFFER_LOCKED = false;
        }
    }
}

impl<T> ImageBuffer<T>
where
    T: Canvas + CanvasBuilder<'static>,
{
    /// Creates a new image buffer with the specified format and size.
    ///
    /// Returns `None` if the buffer is already in use or the
    /// buffer is not big enough to hold the image.
    pub fn new(size: Offset) -> Result<Self, UIError> {
        // SAFETY:
        // It's safe to read/modify mutable static variable as
        // whole app is single-threaded.
        //
        // We can be sure that `IMAGE_BUFFER` is not shared between
        // multiple instances of `ImageBuffer` as we have `IMAGE_BUFFER_LOCKED`
        // to prevent that.
        unsafe {
            if IMAGE_BUFFER_LOCKED {
                return Err(UIError::BufferLocked);
            }

            let bitmap =
                Bitmap::new_mut(T::format(), None, size, None, &mut IMAGE_BUFFER.bytes[..])?;

            IMAGE_BUFFER_LOCKED = true;

            Ok(Self {
                canvas: T::from_bitmap(bitmap),
            })
        }
    }

    /// Returns the canvas for the bitmap in the image buffer.
    pub fn canvas(&mut self) -> &mut T {
        &mut self.canvas
    }

    /// Returns the immutable view of the bitmap in the image buffer.
    pub fn view(&self) -> BitmapView<'_> {
        self.canvas.view()
    }
}
