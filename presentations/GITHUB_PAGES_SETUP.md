# GitHub Pages Setup Guide

## ✅ Files Committed

The following files have been committed and pushed to enable GitHub Pages:

1. **GitHub Actions Workflow**: `.github/workflows/deploy-pages.yml`
   - Automatically deploys presentations on push to main
   - Triggered when files in `presentations/` change

2. **Presentations Index**: `presentations/index.html`
   - Landing page listing all presentations
   - Beautiful dark theme matching presentation style

3. **M25 Presentation**: `presentations/m25_coevolution/`
   - Complete with all 17 images
   - Modal popup functionality for full-screen image viewing
   - Fixed overflow issues

## 🔧 Enable GitHub Pages (One-Time Setup)

### Step 1: Navigate to Repository Settings
1. Go to https://github.com/leybzon/SwarmEvolve
2. Click **Settings** tab
3. Click **Pages** in the left sidebar

### Step 2: Configure Source
1. Under **Build and deployment**:
   - **Source**: Select `GitHub Actions`
   - This will use the workflow file we created

### Step 3: Wait for Deployment
1. Go to **Actions** tab
2. You should see "Deploy GitHub Pages" workflow running
3. Wait for it to complete (usually 1-2 minutes)

### Step 4: Access Your Presentation
Once deployed, your presentations will be available at:

**Main Index**: https://leybzon.github.io/SwarmEvolve/

**M25 Presentation**: https://leybzon.github.io/SwarmEvolve/m25_coevolution/

## 📝 Alternative: Manual Setup (if Actions doesn't work)

If the GitHub Actions workflow doesn't work, you can use the simpler approach:

### Settings → Pages:
- **Source**: Deploy from a branch
- **Branch**: `main`
- **Folder**: `/presentations`

Then access at: https://leybzon.github.io/SwarmEvolve/

## 🎯 What You Get

### Presentations Landing Page
- Clean, professional index of all presentations
- M25 card with description, metadata, and direct link
- Responsive design matching presentation theme

### M25 Presentation Features
- ✅ 17 high-quality data visualizations
- ✅ Full-screen modal popups (click any image)
- ✅ No overflow issues (all slides fit viewport)
- ✅ Reveal.js navigation (arrows, space, ESC)
- ✅ Browser-accessible from anywhere
- ✅ No local server needed

## 🔄 Future Updates

Whenever you push changes to `presentations/`, GitHub Actions will automatically:
1. Detect the change
2. Build and deploy the updated site
3. Make it live within 1-2 minutes

## 🚀 Sharing Your Presentation

Once deployed, you can:
- Share the URL directly: https://leybzon.github.io/SwarmEvolve/m25_coevolution/
- Present from any device with a browser
- No need to download or install anything
- Works on mobile, tablet, desktop

## 📱 Presentation Controls

### Navigation
- **Arrow keys**: Move between slides
- **Space**: Next slide
- **ESC**: Zoom out (overview mode)
- **F**: Fullscreen

### Image Interaction
- **Click image**: Open full-screen modal
- **Click anywhere / ESC**: Close modal

## ✅ Verification Checklist

After setup, verify:
- [ ] GitHub Pages is enabled in repository settings
- [ ] Workflow ran successfully in Actions tab
- [ ] Main index loads: https://leybzon.github.io/SwarmEvolve/
- [ ] M25 presentation loads: https://leybzon.github.io/SwarmEvolve/m25_coevolution/
- [ ] All 17 images display correctly
- [ ] Modal popups work when clicking images
- [ ] No console errors in browser DevTools

## 🐛 Troubleshooting

### Images Not Loading
- Check that `figures/` folder is committed
- Verify image paths are relative (not absolute)
- Check browser console for 404 errors

### Workflow Not Running
- Ensure GitHub Actions are enabled for the repo
- Check `.github/workflows/deploy-pages.yml` exists
- Verify workflow file syntax is valid

### 404 on Base URL
- Make sure GitHub Pages is enabled in Settings
- Wait 1-2 minutes for initial deployment
- Check that workflow completed successfully

## 📊 Repository Structure

```
SwarmEvolve/
├── .github/
│   └── workflows/
│       └── deploy-pages.yml          # Auto-deployment workflow
├── presentations/
│   ├── index.html                    # Landing page
│   └── m25_coevolution/
│       ├── index.html                # M25 presentation
│       ├── figures/                  # 17 PNG images
│       ├── videos/                   # 4 MP4 videos
│       ├── README.md                 # Usage instructions
│       └── *.py                      # Generation scripts
```

## 🎓 Next Steps

1. **Enable GitHub Pages** (follow Step 1-4 above)
2. **Test the presentation** at the deployed URL
3. **Share the link** with your audience
4. **Present!** No local setup required

---

**Support**: If you encounter issues, check the Actions tab for deployment logs or open an issue on GitHub.
