#!/usr/bin/env python3
"""
Add modal popup functionality for images in presentation.
Makes all diagram images clickable to view full-screen.
"""

import re

def add_image_modal():
    """Add modal popup for images"""

    with open('index.html', 'r') as f:
        html = f.read()

    # Add modal HTML before closing </body>
    modal_html = '''
    <!-- Image Modal -->
    <div id="imageModal" style="display: none; position: fixed; z-index: 9999; left: 0; top: 0; width: 100%; height: 100%; overflow: auto; background-color: rgba(0, 0, 0, 0.95); cursor: pointer;">
        <img id="modalImage" style="margin: auto; display: block; max-width: 95%; max-height: 95%; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); border-radius: 8px; box-shadow: 0 0 50px rgba(255, 255, 255, 0.3);">
        <div style="position: absolute; top: 20px; right: 40px; color: #fff; font-size: 40px; font-weight: bold; cursor: pointer;">&times;</div>
    </div>

    <script>
        // Image modal functionality
        document.addEventListener('DOMContentLoaded', function() {
            const modal = document.getElementById('imageModal');
            const modalImg = document.getElementById('modalImage');

            // Add click handlers to all diagram images
            document.querySelectorAll('.diagram-container img').forEach(img => {
                img.style.cursor = 'pointer';
                img.addEventListener('click', function(e) {
                    e.stopPropagation();
                    modal.style.display = 'block';
                    modalImg.src = this.src;
                });
            });

            // Close modal on click
            modal.addEventListener('click', function() {
                modal.style.display = 'none';
            });

            // Close modal on ESC key
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape' && modal.style.display === 'block') {
                    modal.style.display = 'none';
                }
            });
        });
    </script>
'''

    # Insert modal before </body>
    html = html.replace('</body>', modal_html + '\n</body>')

    # Add hover effect to diagram-container images in CSS
    css_addition = '''
        .diagram-container img {
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .diagram-container img:hover {
            transform: scale(1.02);
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
        }
'''

    # Insert CSS before closing </style>
    html = html.replace('    </style>', css_addition + '    </style>')

    with open('index.html', 'w') as f:
        f.write(html)

    print("✅ Added image modal popup functionality")
    print("Features:")
    print("  - Click any diagram to view full-screen")
    print("  - Close by clicking anywhere or pressing ESC")
    print("  - Hover effect on images")

if __name__ == '__main__':
    add_image_modal()
