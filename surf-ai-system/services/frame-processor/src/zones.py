class ZoneCalculator:
    def __init__(self, frame_width: int):
        self.frame_width = frame_width
        self.third = frame_width / 3.0

    def get_zone(self, bbox: list) -> str:
        """
        bbox: [x1, y1, x2, y2]
        Uses explicit center X spatial coordinate logically aligned natively bounding boxes.
        """
        x1, y1, x2, y2 = bbox
        center_x = (x1 + x2) / 2.0
        
        if center_x < self.third:
            return "left"
        elif center_x < 2 * self.third:
            return "center"
        else:
            return "right"
