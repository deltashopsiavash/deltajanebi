from PIL import Image, ImageDraw
from django.test import SimpleTestCase

from shop.services import source_sanitizer as legacy
from shop.services import source_sanitizer_v22 as sanitizer


class StrictImageSanitizerV27Tests(SimpleTestCase):
    def test_cleanup_version_forces_old_cached_images_to_be_rebuilt(self):
        self.assertEqual(sanitizer.CLEANUP_VERSION, "7")

    def test_component_mask_discards_detached_logo_inside_crop_area(self):
        mask = Image.new("L", (220, 220), 0)
        draw = ImageDraw.Draw(mask)
        draw.rectangle((70, 45, 150, 175), fill=255)   # product
        draw.rectangle((160, 95, 185, 115), fill=255)  # detached logo/text

        components = legacy._components(mask)
        selected = sanitizer._choose_subject_components(components, 220, 220)
        self.assertTrue(selected)
        product_only = sanitizer._subject_component_mask(mask, selected)

        self.assertGreater(product_only.getpixel((110, 100)), 0)
        self.assertEqual(product_only.getpixel((170, 105)), 0)

    def test_real_cleaner_removes_detached_colored_ad_even_when_crop_would_include_it(self):
        image = Image.new("RGB", (700, 700), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((230, 145, 470, 570), fill=(20, 20, 20))
        # Deliberately close to the product. A plain bounding-box crop would
        # retain part of this red source logo/ad; the component mask must not.
        draw.rectangle((475, 250, 530, 300), fill=(220, 20, 20))

        cleaned = sanitizer._strict_studio_clean(image)
        self.assertIsNotNone(cleaned)
        self.assertEqual(cleaned.width, cleaned.height)
        pixels = list(cleaned.getdata())
        red_ad_pixels = sum(1 for r, g, b in pixels if r > 150 and r > g * 2 and r > b * 2)
        dark_product_pixels = sum(1 for r, g, b in pixels if max(r, g, b) < 80)
        self.assertEqual(red_ad_pixels, 0)
        self.assertGreater(dark_product_pixels, 1000)
