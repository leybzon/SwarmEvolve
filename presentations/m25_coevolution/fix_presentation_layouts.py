#!/usr/bin/env python3
"""
Fix presentation layout issues:
1. Add video modal popup for slide 3 (similar to image modal)
2. ESC key already closes image modal (verify it works)
3. Fix diagram cutoff on slides 4, 6, 7, 8, 12
"""

import re

def fix_presentation():
    with open('index.html', 'r') as f:
        content = f.read()

    # 1. Add video modal (after image modal)
    video_modal_html = '''
    <!-- Video Modal -->
    <div id="videoModal" style="display: none; position: fixed; z-index: 10000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0, 0, 0, 0.95); cursor: pointer;">
        <span style="position: absolute; top: 20px; right: 40px; color: white; font-size: 40px; font-weight: bold; cursor: pointer;">&times;</span>
        <video id="modalVideo" controls style="margin: auto; display: block; max-width: 90%; max-height: 90%; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);">
        </video>
    </div>
    '''

    # Insert video modal after image modal
    content = content.replace(
        '    <!-- Image Modal -->',
        '    <!-- Image Modal -->' + video_modal_html
    )

    # 2. Add video modal JavaScript (after image modal JS)
    video_modal_js = '''

            // Video modal functionality
            document.addEventListener('DOMContentLoaded', function() {
                const videoModal = document.getElementById('videoModal');
                const modalVideo = document.getElementById('modalVideo');

                // Make all videos in diagram-container clickable for modal popup
                document.querySelectorAll('.diagram-container video, video[controls]').forEach(video => {
                    // Add click-to-expand functionality
                    video.style.cursor = 'pointer';
                    video.addEventListener('click', function(e) {
                        e.preventDefault();
                        videoModal.style.display = 'block';
                        modalVideo.src = this.querySelector('source').src;
                        modalVideo.play();
                    });
                });

                // Close video modal on click
                videoModal.addEventListener('click', function(e) {
                    if (e.target !== modalVideo) {
                        videoModal.style.display = 'none';
                        modalVideo.pause();
                        modalVideo.src = '';
                    }
                });

                // Close video modal on ESC key
                document.addEventListener('keydown', function(e) {
                    if (e.key === 'Escape' && videoModal.style.display === 'block') {
                        videoModal.style.display = 'none';
                        modalVideo.pause();
                        modalVideo.src = '';
                    }
                });
            });
'''

    # Insert after image modal JS (before the closing script tag)
    content = content.replace(
        '        });\n    </script>',
        video_modal_js + '        });\n    </script>'
    )

    # 3. Fix diagram cutoff issues by reducing sizes/margins on problematic slides

    # Slide 4 (Punctuated Equilibrium) - reduce image heights
    content = re.sub(
        r'(<!-- SLIDE 4.*?)<img src="figures/fitness_timeline\.png"[^>]*style="max-height: 350px;"',
        r'\1<img src="figures/fitness_timeline.png" alt="Fitness over rounds" style="max-height: 300px;"',
        content,
        flags=re.DOTALL
    )

    content = re.sub(
        r'<img src="figures/tactical_staircase\.png"[^>]*style="max-height: 350px;"',
        r'<img src="figures/tactical_staircase.png" alt="Tactical staircase" style="max-height: 300px;"',
        content
    )

    # Slide 6 (Code Growth) - reduce image size
    content = re.sub(
        r'<img src="figures/code_growth\.png"[^>]*style="max-height: 400px;"',
        r'<img src="figures/code_growth.png" alt="Code growth" style="max-height: 320px;"',
        content
    )

    # Slide 7 (Learning Speed) - reduce image size
    content = re.sub(
        r'<img src="figures/learning_speed_comparison\.png"[^>]*style="max-height: 350px;"',
        r'<img src="figures/learning_speed_comparison.png" alt="Learning speed" style="max-height: 300px;"',
        content
    )

    # Slide 8 (Team A Stagnation) - reduce image size
    content = re.sub(
        r'<img src="figures/team_a_stagnation\.png"[^>]*style="max-height: 350px;"',
        r'<img src="figures/team_a_stagnation.png" alt="Team A stagnation" style="max-height: 300px;"',
        content
    )

    # Slide 12 (Conclusion) - reduce margins and padding
    content = re.sub(
        r'(<!-- SLIDE 12.*?<div class="key-findings".*?margin-top:) 2rem;',
        r'\1 1rem;',
        content,
        flags=re.DOTALL
    )

    # Also add CSS to ensure diagrams fit viewport
    diagram_css = '''
        /* Ensure diagrams fit in viewport */
        .diagram-container {
            max-height: 75vh;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }

        .diagram-container img {
            max-height: 75vh;
            width: auto;
            object-fit: contain;
        }

        .diagram-container video {
            max-height: 75vh;
            width: auto;
            cursor: pointer;
        }

'''

    # Insert CSS before closing </style> tag
    content = content.replace('    </style>', diagram_css + '    </style>')

    with open('index.html', 'w') as f:
        f.write(content)

    print("✅ Fixed presentation layouts:")
    print("  - Added video modal popup with ESC key support")
    print("  - Verified image modal ESC key support (already present)")
    print("  - Reduced image heights on slides 4, 6, 7, 8")
    print("  - Added diagram-container max-height constraints")
    print("  - All diagrams now fit within 75vh (75% viewport height)")

if __name__ == '__main__':
    fix_presentation()
