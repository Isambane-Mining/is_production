from io import BytesIO
import unittest
from pptx import Presentation
from is_production.production.report.hod_presentation.hod_presentation import build_hod_presentation


class TestDashboardExport(unittest.TestCase):
    def test_dashboard_capture_is_one_fitted_slide_without_a_duplicate_heading(self):
        pixel = 'iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAIAAAACUFjqAAAAFUlEQVR4nGP8//8/A27AhEduBEsDAKXjAxF9kqZqAAAAAElFTkSuQmCC'
        output = BytesIO()
        build_hod_presentation({'site':'Uitgevallen','captured_slides':[
            {'title':'Uitgevallen · Production Performance','heading_style':'dashboard',
             'image_data':'data:image/png;base64,'+pixel}]}, output)
        output.seek(0)
        deck = Presentation(output)
        self.assertEqual(len(deck.slides), 1)
        pictures = [shape for shape in deck.slides[0].shapes if shape.shape_type == 13]
        self.assertEqual(len(pictures), 1)
        self.assertEqual([s.text for s in deck.slides[0].shapes if s.has_text_frame], ['1'])
        picture = pictures[0]
        self.assertGreaterEqual(picture.left, 0)
        self.assertGreaterEqual(picture.top, 0)
        self.assertLessEqual(picture.left+picture.width, deck.slide_width+2)
        self.assertLessEqual(picture.top+picture.height, deck.slide_height+2)

    def test_two_sites_export_two_dashboard_slides(self):
        pixel = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAIAAAACUFjqAAAAFUlEQVR4nGP8//8/A27AhEduBEsDAKXjAxF9kqZqAAAAAElFTkSuQmCC'
        captures = [
            {'title':f'{site} · Production Performance', 'heading_style':'dashboard', 'image_data':pixel}
            for site in ('Uitgevallen','Klipfontein')
        ]
        output = BytesIO()
        build_hod_presentation({'site':'Uitgevallen / Klipfontein','captured_slides':captures},output)
        output.seek(0)
        deck = Presentation(output)
        self.assertEqual(len(deck.slides),2)
        for slide in deck.slides:
            self.assertEqual([s.text for s in slide.shapes if s.has_text_frame], [str(deck.slides.index(slide) + 1)])
            self.assertEqual(len([shape for shape in slide.shapes if shape.shape_type == 13]),1)
