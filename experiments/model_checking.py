"""Methods for performing bounded model checking on neural net controllers.
"""
import itertools
from timeit import default_timer as timer
import numpy as np
import pyclipper
import pypoman
from experiments.experiment import Experiment
from experiments.vrl_model import VRLModel

class ModelCheckingExperiment(Experiment):
    """Experiment that performs BMC on three NN controller models.

    Models are from the project: https://github.com/caffett/VRL_CodeReview
    """
    @staticmethod
    def box_to_constraints(bounds):
        """Converts a set of (uniform) bounds to H-representation.

        Returns A_ub such that a point x is in the box defined by bounds[0] <=
        x[0] <= bounds[1] and bounds[0] <= x[0] <= bounds[1] if (and only if)
        A_ub*x <= 0.
        """
        return np.array([
            [1, 0, -bounds[1]],
            [0, 1, -bounds[1]],
            [-1, 0, bounds[0]],
            [0, -1, bounds[0]],
        ])

    @staticmethod
    def box_to_vertices(bounds):
        """Extracts a V-representation from a box.

        @bounds should be a tuple (low, high), the box is taken to be:
        { (x, y) | low <= x <= high ^ low <= y <= high }
        """
        low, high = bounds
        return np.array([
            [low, low],
            [high, low],
            [high, high],
            [low, high],
        ])

    @staticmethod
    def facet_enumeration(plane):
        """Converts a V-representation convex polytope to H-representation.

        Assumes that @plane is a V-representation, convex polytope with
        vertices in counter-clockwise order.
        """
        A_ub = []
        b_ub = []
        # We want to get *all* edges, including the last one, so we need it to
        # "wrap around."
        edge_endpoints = np.append(plane[1:, :], [plane[0, :]], axis=0)
        for from_vertex, to_vertex in zip(plane, edge_endpoints):
            norm = np.array([to_vertex[0] - from_vertex[0],
                             from_vertex[1] - to_vertex[1]])
            offset = np.dot(norm, from_vertex)
            avg_b = np.mean(np.matmul(plane, norm))
            if avg_b < offset:
                A_ub.append(norm)
                b_ub.append(offset)
            else:
                A_ub.append(-norm)
                b_ub.append(-offset)
        return np.array(A_ub), np.array(b_ub)

    @staticmethod
    def compute_intersection(plane1, plane2):
        """Computes the intersection of two V-representation polygons.
        """
        try:
            return pypoman.intersection.intersect_polygons(plane1, plane2)
        except pyclipper.ClipperException:
            # This error is thrown when floating point issues cause at least
            # one of the polytopes to "fold in on itself." As long as their
            # over-approximating boxes are disjoint, we can safely return [] as
            # they cannot intersect.
            min1, max1 = np.min(plane1, axis=0), np.max(plane1, axis=0)
            min2, max2 = np.min(plane2, axis=0), np.max(plane2, axis=0)
            are_certainly_disjoint = np.any(max1 < min2) or np.any(max2 < min1)
            if are_certainly_disjoint:
                return []
            assert NotImplementedError

    @classmethod
    def transition_plane(cls, model, network, plane, transition_partitions):
        """Computes post-set through entire model + environment for a pre-set.

        Essentially, given a set of transition partitions (i.e., regions of the
        input space for which the transition function is affine), we intersect
        the plane with each of the transitions; and transition the intersection
        accordingly.
        """
        A_ub, b_ub = cls.facet_enumeration(plane)
        resulting_planes = []
        for pre_plane in transition_partitions:
            pre_intersection = cls.compute_intersection(pre_plane, plane)
            if pre_intersection:
                actions = network.compute(pre_intersection)
                resulting_planes.append(np.array([
                    model.env_step(pre_intersection[i], actions[i])
                    for i in range(len(pre_intersection))]))
        return resulting_planes

    @staticmethod
    def in_h_rep(plane, faces):
        """True if @plane is contained in @faces.

        @plane should be a polytope in V-representation.
        @faces should be a polytope in H-representation.

        Used for a number of purposes, most importantly to check if any of the
        post-set has intersected with the unsafe regions.
        """
        # plane is (n_points x dims) faces is (n_constraints x dims + 1)
        product = np.matmul(faces[:, :-1], plane.T) # (n_constraints x n_points)
        value = product.T + faces[:, -1] # (n_points x n_constraints)
        return np.all(value <= 0)

    def run_for_model(self, model_name, timeout_minutes):
        """Runs the BMC for a particular model.
        """
        network = self.load_network("vrl_%s" % model_name)
        model = VRLModel(model_name)

        out_file = self.begin_csv("%s/data" % model_name,
                                  ["Step", "Cumulative Time", "Counter Example?"])

        safe = model.safe_set(as_box=False)
        init = model.init_set(as_box=False)
        disjunctive_safe = model.disjunctive_safe_set()

        time_taken = 0.0

        start_time = timer()
        safe_transformed = network.transform_planes(disjunctive_safe,
                                                    compute_preimages=True,
                                                    include_post=False)
        safe_transitions = []
        for transformed_plane in safe_transformed:
            safe_transitions.extend(transformed_plane)
        time_taken += (timer() - start_time)

        planes = [model.init_set(as_vertices=True)]
        for step_i in itertools.count():
            print("Step:", step_i + 1)
            print("Planes:", len(planes))
            start_time = timer()
            new_planes = []
            for plane in planes:
                new_planes.extend(
                    self.transition_plane(model, network, plane,
                                          safe_transitions))
            new_planes = list(map(np.array, new_planes))
            print("Before removing in init_x/y:", len(new_planes))
            new_planes = [plane for plane in new_planes
                          if not self.in_h_rep(plane, init)]
            found_bad_state = False
            for plane in new_planes:
                if not self.in_h_rep(plane, safe):
                    print("Dangerous behavior found!")
                    found_bad_state = True
                    break
            planes = new_planes
            time_taken += timer() - start_time

            self.write_csv(out_file, {
                "Step": step_i + 1,
                "Cumulative Time": time_taken,
                "Counter Example?": found_bad_state
            })
            if found_bad_state:
                break
            if time_taken > (timeout_minutes * 60):
                break

    def run(self):
        """Runs the experiment for all three models.
        """
        models = ["pendulum_continuous", "satelite", "quadcopter"]
        timeout = int(input("Timeout (per-model, minutes): "))
        for model in models:
            print("Model:", model)
            self.run_for_model(model, timeout)

    def analyze(self):
        """No analysis needed for the BMC experiment.

        (Plots produced directly in the LaTeX with PGFPlots)
        """
        return False

if __name__ == "__main__":
    ModelCheckingExperiment("model_checking").main()
